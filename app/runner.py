"""Corrida diaria: revisa contratos, descarga las facturas que cortan hoy y las envia."""
from __future__ import annotations

import calendar
import html as _html
import shutil
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

# Bloqueo de archivo para que dos corridas no se pisen.
# Ojo: en Linux `msvcrt` no existe; si se consulta con hasattr() se cae con
# NameError, que es justo lo que pasaba en WSL al pulsar "Buscar facturas".
if sys.platform == "win32":          # Windows
    import msvcrt as _lockmod

    def _tomar_bloqueo(archivo) -> None:
        _lockmod.locking(archivo.fileno(), _lockmod.LK_NBLCK, 1)

    def _soltar_bloqueo(archivo) -> None:
        _lockmod.locking(archivo.fileno(), _lockmod.LK_UNLCK, 1)
else:                                # Linux / macOS
    import fcntl as _lockmod

    def _tomar_bloqueo(archivo) -> None:
        _lockmod.flock(archivo, _lockmod.LOCK_EX | _lockmod.LOCK_NB)

    def _soltar_bloqueo(archivo) -> None:
        _lockmod.flock(archivo, _lockmod.LOCK_UN)

from . import config, db, naming, telegram
from .edenorte import EdenorteClient, PdfNoDisponible, PortalError
from .pdfdata import DatosFactura, datos_de_archivo
from .proveedores import ALERTA as ALERTA_PROVEEDOR, EdesurPortal, portal_de

TIPO_CONSUMO = "consumo"

_local = threading.local()


# portales con sesion abierta en la corrida en curso: se cierran al terminar
_abiertos: list = []


def _anotar_portales(portales: dict) -> None:
    _abiertos.extend(portales.values())


def _con_cierre_de_sesiones(fn):
    """Cierra las sesiones que abrio la llamada, tambien si hubo error."""
    import functools

    @functools.wraps(fn)
    def envoltura(*args, **kwargs):
        antes = len(_abiertos)
        try:
            return fn(*args, **kwargs)
        finally:
            for portal in _abiertos[antes:]:
                cerrador = getattr(portal, "cerrar", None)
                if callable(cerrador):
                    try:
                        cerrador()
                    except Exception:  # noqa: BLE001 - cerrar nunca debe romper
                        pass
            del _abiertos[antes:]

    return envoltura


@contextmanager
def _exclusivo():
    """True si nadie mas esta corriendo. Bloquea por proceso y entre procesos.

    El panel lanza la sincronizacion en un hilo y la tarea de Windows corre en
    otro proceso: sin esto se duplican descargas, albumes y pendientes.
    """
    if getattr(_local, "tomado", False):        # llamada anidada (mismo hilo)
        yield True
        return
    ruta = config.DB_PATH.parent / "runner.lock"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    archivo = open(ruta, "a+b")
    tomado = False
    try:
        try:
            _tomar_bloqueo(archivo)
            tomado = True
        except OSError:
            tomado = False
        if tomado:
            _local.tomado = True
        yield tomado
    finally:
        if tomado:
            _local.tomado = False
            try:
                _soltar_bloqueo(archivo)
            except OSError:
                pass
        archivo.close()
ALERTA_PORTAL = ALERTA_PROVEEDOR["edenorte"]   # compatibilidad


# ------------------------------------------------------------------ utilidades
def corta_hoy(dia_corte: int | None, fecha: date) -> bool:
    """True si el contrato tiene su corte en la fecha dada (mes corto => ultimo dia)."""
    if not dia_corte:
        return False
    ultimo = calendar.monthrange(fecha.year, fecha.month)[1]
    esperado = min(int(dia_corte), ultimo)
    return fecha.day == esperado


_MESES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
          "aug": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dec": 12, "dic": 12,
          "ene": 1, "abr": 4, "ago": 8}


def fecha_portal(texto: str | None) -> str | None:
    """Fecha del listado del portal -> AAAA-MM-DD (o None si no se entiende)."""
    import re as _re

    t = (texto or "").strip()
    if not t:
        return None
    m = _re.match(r"^(\d{4})-(\d{2})-(\d{2})$", t)
    if m:
        return t
    m = _re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", t)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = _re.match(r"^(\d{1,2})\s+([A-Za-zÁÉÍÓÚáéíóú]{3,})\.?\s+(\d{4})$", t)
    if m:
        mes = _MESES.get(m.group(2).lower()[:3])
        if mes:
            return f"{m.group(3)}-{mes:02d}-{int(m.group(1)):02d}"
    return None


def es_consumo(tipo: str) -> bool:
    return TIPO_CONSUMO in (tipo or "").lower()


@dataclass
class Resultado:
    fecha: str
    descargadas: list[dict] = field(default_factory=list)
    pendientes: list[dict] = field(default_factory=list)
    descartadas: list[dict] = field(default_factory=list)
    portal_error: str | None = None
    mensaje_enviado: bool = False
    detalle_envio: str = ""
    historico: dict = field(default_factory=dict)

    def como_dict(self) -> dict:
        return {
            "fecha": self.fecha,
            "descargadas": self.descargadas,
            "pendientes": self.pendientes,
            "descartadas": self.descartadas,
            "portal_error": self.portal_error,
            "mensaje_enviado": self.mensaje_enviado,
            "historico": self.historico,
        }


def _guardar_pdf(origen: bytes, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".part")
    tmp.write_bytes(origen)
    tmp.replace(destino)
    return destino


def _candidato_archivo(nic: str, datos: DatosFactura, razon_social: str,
                       carpeta: Path, ref_pago: str | None = None) -> Path:
    """Nombre libre para el PDF. Nunca sobreescribe una factura distinta.

    Orden: `NOMBRE NIC.pdf` -> `NOMBRE NIC (corte).pdf` -> `... (corte 2).pdf`...
    (puede haber dos facturas con el mismo corte, p.ej. una nota de credito).
    """
    nombre = naming.nombre_archivo(razon_social or (datos.titular or ""), nic)
    destino = carpeta / nombre
    if not destino.exists():
        return destino
    raiz = f"{destino.stem} ({datos.corte or 'sin-corte'})"
    destino = carpeta / f"{raiz}.pdf"
    n = 2
    while destino.exists():
        destino = carpeta / f"{raiz} {n}.pdf"
        n += 1
    return destino


def _fecha_legible(iso: str | None) -> str:
    """2026-11-15 -> 15/11/2026."""
    if not iso:
        return ""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return iso


def _caption(fecha: str, nuevas: list[dict], pendientes: list[dict]) -> str:
    """Solo lo que pide el cliente: NIC, nombre normalizado, a pagar y asesor.

    La fecha (vencimiento) se agrega unicamente si la factura la trae.
    """
    bloques = []
    for f in nuevas:
        nombre = naming.normalizar_razon_social(f.get("razon_social") or "")
        # parse_mode=HTML: un nombre con <, > o & haria fallar el envio
        lineas = [f"NIC {f['nic']} · {_html.escape(nombre)}"]
        monto = f"{f['facturado']:,.2f}" if f.get("facturado") is not None else "s/d"
        lineas.append(f"Pagar: RD${monto}")
        if f.get("asesor"):
            lineas.append(f"Asesor: {_html.escape(str(f['asesor']))}")
        vence = _fecha_legible(f.get("vence"))
        if vence:
            lineas.append(f"Vence: {vence}")
        bloques.append("\n".join(lineas))
    return "\n\n".join(bloques)


def _caption_pendientes(pendientes: list[dict]) -> str:
    """Aviso aparte para lo que corto y no esta publicado."""
    lineas = ["Pendientes (cortaron y aun no estan en el portal):"]
    for p in pendientes:
        lineas.append(f"NIC {p['nic']} · corte {_fecha_legible(p['fecha_corte'])} · "
                      f"intento {p['intentos']}")
    return "\n".join(lineas)


# --------------------------------------------------------------------- corrida
def corrida(fecha: date | None = None, enviar: bool = True,
            chat_id: str | None = None, cliente: EdenorteClient | None = None,
            reporte: bool = False, portales: dict | None = None,
            historico: bool = True) -> Resultado:
    """Revisa Edenorte y Edesur. Cada distribuidora se trata por separado: si una
    falla, la otra sigue y se avisa por Telegram cual fallo."""
    fecha = fecha or date.today()
    fiso = fecha.isoformat()
    res = Resultado(fecha=fiso)
    propios = portales is None
    if propios:
        portales = {"edenorte": cliente or EdenorteClient()}
        if config.EDESUR_TOKEN or (config.EDESUR_USER and config.EDESUR_PASSWORD):
            portales["edesur"] = EdesurPortal()
        _anotar_portales(portales)

    with db.sesion() as conn:
        # 1. sesion en cada distribuidora
        activos: dict[str, object] = {}
        for nombre_prov, portal in portales.items():
            try:
                portal.login()
                activos[nombre_prov] = portal
            except PortalError as exc:
                if "SIN_CREDENCIALES" in str(exc):
                    db.log_evento(conn, "aviso",
                                  f"{nombre_prov}: sin credenciales en .env, se omite",
                                  str(exc)[:200])
                    continue
                alerta = ALERTA_PROVEEDOR.get(nombre_prov, ALERTA_PORTAL)
                res.portal_error = f"{nombre_prov}: {exc}"
                db.log_evento(conn, "error", alerta, str(exc))
                if enviar:
                    try:
                        telegram.enviar_mensaje(
                            f"⚠️ <b>{alerta}</b>\nNo se pudo iniciar sesión el {fiso}."
                            f"\nDetalle: {str(exc)[:300]}", chat_id=chat_id)
                        res.mensaje_enviado = True
                    except telegram.TelegramError as terr:
                        db.log_evento(conn, "error", "No se pudo enviar la alerta a Telegram",
                                      str(terr))
            except Exception as exc:  # credenciales ausentes, red, etc.
                db.log_evento(conn, "error",
                              ALERTA_PROVEEDOR.get(nombre_prov, ALERTA_PORTAL), str(exc)[:300])

        if not activos:
            return res

        # 2. sincronizar contratos de cada portal (queda marcada su distribuidora)
        del_portal: set[str] = set()
        for nombre_prov, portal in activos.items():
            try:
                for c in portal.contratos():
                    del_portal.add(c.nic)
                    existente = db.obtener_contrato(conn, c.nic)
                    if existente is None:
                        # los clientes se dan de alta desde el panel, no se importan
                        # en masa desde la cuenta de la distribuidora
                        continue
                    if (existente["proveedor"] if "proveedor" in existente.keys()
                            else "") != nombre_prov:
                        db.upsert_contrato(conn, c.nic, proveedor=nombre_prov)
                    if not existente["razon_social"] and c.titular:
                        db.upsert_contrato(conn, c.nic, razon_social=c.titular,
                                           direccion=c.direccion, telefono=c.telefono,
                                           proveedor=nombre_prov)
                    elif not existente["dia_corte"]:
                        aprender = getattr(portal, "ultimo_corte", None)
                        if callable(aprender):
                            try:
                                dia = aprender(c.nic)
                            except PortalError:
                                dia = None
                            if isinstance(dia, int) and 1 <= dia <= 31:
                                db.upsert_contrato(conn, c.nic, dia_corte=dia,
                                                   proveedor=nombre_prov)
            except PortalError as exc:
                db.log_evento(conn, "aviso",
                              f"No se pudieron listar los contratos de {nombre_prov}", str(exc))

        # contratos registrados en el dashboard que aun no estan en la cuenta
        for contrato in db.listar_contratos(conn):
            nic_db = str(contrato["nic"])
            if nic_db in del_portal:
                continue
            prov = (contrato["proveedor"] if "proveedor" in contrato.keys() else "") or "edenorte"
            portal = activos.get(prov)
            asociar = getattr(portal, "asociar_contrato", None)
            if portal is None or not callable(asociar):
                continue
            try:
                nuevo_contrato = asociar(nic_db, espera_max=45)
                titular = getattr(nuevo_contrato, "titular", "")
                if not isinstance(titular, str) or not titular.strip():
                    raise ValueError("el portal no devolvio el titular; "
                                     "no se toca lo que ya esta guardado")
                db.upsert_contrato(
                    conn, nic_db,
                    razon_social=titular.strip(),
                    direccion=str(getattr(nuevo_contrato, "direccion", "") or ""),
                    telefono=str(getattr(nuevo_contrato, "telefono", "") or ""),
                    proveedor=prov,
                )
                db.log_evento(conn, "info", f"Contrato {nic_db} asociado a la cuenta",
                              "desde la corrida diaria")
            except Exception as exc:  # una asociacion fallida no tumba la corrida
                db.log_evento(conn, "aviso", f"No se pudo asociar el contrato {nic_db}",
                              str(exc)[:300])

        # 3. objetivos: pendientes abiertos + contratos que cortan hoy
        objetivos: dict[str, set[str]] = {}
        for p in db.pendientes_abiertos(conn):
            objetivos.setdefault(str(p["nic"]), set()).add(p["fecha_corte"])
        for c in db.listar_contratos(conn):
            if not c["activo"]:
                continue
            prov = (c["proveedor"] if "proveedor" in c.keys() else "") or "edenorte"
            if prov in activos and corta_hoy(c["dia_corte"], fecha):
                objetivos.setdefault(str(c["nic"]), set()).add(fiso)

        if not objetivos:
            db.log_evento(conn, "info", f"Sin cortes ni pendientes para {fiso}")
            if reporte and enviar:
                try:
                    telegram.enviar_mensaje(f"Sin facturas con corte {fiso}.", chat_id=chat_id)
                    res.mensaje_enviado = True
                except telegram.TelegramError as exc:
                    db.log_evento(conn, "error", "Fallo el envio del reporte", str(exc)[:300])
            # igual se mantiene el historico (y se manda lo que quedo pendiente)
            if historico:
                try:
                    res.historico = sincronizar_historial(fecha=fecha, enviar=enviar,
                                                          chat_id=chat_id, portales=portales)
                except Exception as exc:  # noqa: BLE001
                    res.historico = {"errores": [str(exc)[:200]]}
            return res

        # 4. revisar cada contrato objetivo
        for nic, cortes in objetivos.items():
            contrato = db.obtener_contrato(conn, nic)
            if not contrato or not contrato["activo"]:
                db.log_evento(conn, "aviso", f"NIC {nic} inactivo o inexistente: se omite")
                continue
            razon_social = contrato["razon_social"] if contrato else ""
            prov = (contrato["proveedor"] if contrato and "proveedor" in contrato.keys()
                    else "") or "edenorte"
            portal = activos.get(prov)
            if portal is None:
                db.log_evento(conn, "aviso",
                              f"NIC {nic}: {prov} no esta disponible en esta corrida")
                continue
            try:
                hist = portal.historial(nic)
            except PortalError as exc:
                db.log_evento(conn, "error", f"Historial no disponible para NIC {nic}", str(exc))
                for fc in cortes:
                    db.registrar_pendiente(conn, nic, fc, f"historial: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - red, parseo, lo que sea
                db.log_evento(conn, "error", f"Fallo inesperado en NIC {nic}", str(exc)[:300])
                continue

            candidatas = [f for f in hist.filas
                          if es_consumo(f.tipo) and not db.factura_existe(conn, f.ref_pago)]
            # cortes que ya tienen su factura descargada: no son pendientes
            ya_descargados = {r["corte"] for r in conn.execute(
                "SELECT corte FROM facturas WHERE nic = ? AND estado = 'descargada'", (nic,))
                if r["corte"]}
            encontrado_por_corte: set[str] = {c for c in cortes if c in ya_descargados}

            for fila in candidatas[:4]:  # el portal solo conserva las 2 mas recientes
                try:
                    contenido = portal.descargar_pdf(fila.ref_pago)
                except PortalError as exc:
                    db.log_evento(conn, "aviso", f"PDF no disponible {fila.ref_pago} ({nic})", str(exc))
                    continue

                with tempfile.TemporaryDirectory() as tmp:
                    tmp_pdf = Path(tmp) / "f.pdf"
                    tmp_pdf.write_bytes(contenido)
                    try:
                        datos = datos_de_archivo(tmp_pdf, proveedor=prov)
                    except Exception as exc:
                        db.log_evento(conn, "error", f"No se pudo leer el PDF {fila.ref_pago}", str(exc))
                        continue

                if datos.corte not in cortes:
                    res.descartadas.append({"nic": nic, "ref_pago": fila.ref_pago,
                                            "corte": datos.corte, "motivo": "corte distinto"})
                    # el archivo se guarda igual: es una factura real del cliente
                    destino = _candidato_archivo(nic, datos, contrato["razon_social"] or "",
                                                 config.FACTURAS_DIR)
                    _guardar_pdf(contenido, destino)
                    db.guardar_factura(conn, {
                        "nic": nic, "ref_pago": fila.ref_pago, "tipo": fila.tipo,
                        "cliente": datos.titular or fila.cliente,
                        "periodo_ini": datos.periodo_ini, "periodo_fin": datos.periodo_fin,
                        "corte": datos.corte, "emision": datos.emision, "vence": datos.vence,
                        "kwh": datos.kwh, "facturado": datos.facturado,
                        "pdf_path": str(destino), "estado": "descartada",
                    })
                    continue
                if datos.kwh is None:
                    res.descartadas.append({"nic": nic, "ref_pago": fila.ref_pago,
                                            "corte": datos.corte, "motivo": "sin consumo (en blanco)"})
                    db.guardar_factura(conn, {
                        "nic": nic, "ref_pago": fila.ref_pago, "tipo": fila.tipo,
                        "cliente": datos.titular or fila.cliente,
                        "periodo_ini": datos.periodo_ini, "periodo_fin": datos.periodo_fin,
                        "corte": datos.corte, "emision": datos.emision, "vence": datos.vence,
                        "facturado": datos.facturado, "estado": "sin_consumo",
                    })
                    continue

                destino = _candidato_archivo(nic, datos, razon_social, config.FACTURAS_DIR)
                _guardar_pdf(contenido, destino)

                id_guardado = db.guardar_factura(conn, {
                    "nic": nic,
                    "ref_pago": fila.ref_pago,
                    "no_factura": datos.no_factura,
                    "tipo": fila.tipo,
                    "cliente": datos.titular or fila.cliente,
                    "periodo_ini": datos.periodo_ini,
                    "periodo_fin": datos.periodo_fin,
                    "corte": datos.corte,
                    "emision": datos.emision,
                    "vence": datos.vence,
                    "dias": datos.dias,
                    "kwh": datos.kwh,
                    "sin_subsidio": datos.sin_subsidio,
                    "subsidio": datos.subsidio,
                    "facturado": datos.facturado,
                    "pdf_path": str(destino),
                    "estado": "descargada",
                })
                if datos.corte and not contrato_dia_corte(conn, nic):

                    # el dia de corte se aprende una vez; no se pisa el del panel

                    db.upsert_contrato(conn, nic,

                                   razon_social=razon_social or (datos.titular or ""),

                                   dia_corte=int(datos.corte[-2:]))
                db.cerrar_pendiente(conn, nic, datos.corte)
                encontrado_por_corte.add(datos.corte)
                res.descargadas.append({
                    "id": id_guardado,
                    "nic": nic,
                    "ref_pago": fila.ref_pago,
                    "razon_social": razon_social or (datos.titular or ""),
                    "alias": contrato["alias"] if contrato else "",
                    "asesor": contrato["asesor"] if contrato else "",
                    "corte": datos.corte,
                    "vence": datos.vence,
                    "facturado": datos.facturado,
                    "kwh": datos.kwh,
                    "path": str(destino),
                })

            for fc in cortes:
                if fc in encontrado_por_corte:
                    continue
                if any(d["nic"] == nic and d["corte"] == fc for d in res.descartadas):
                    continue
                db.registrar_pendiente(conn, nic, fc, "sin factura publicada")
                res.pendientes.append({"nic": nic, "fecha_corte": fc})
            db.log_evento(conn, "info", f"Revisado NIC {nic}", f"cortes={sorted(cortes)}")

        # refrescar pendientes con sus intentos reales
        abiertos = {str(p["nic"]) + p["fecha_corte"]: dict(p) for p in db.pendientes_abiertos(conn)}
        res.pendientes = [abiertos[k] for k in
                          (str(p["nic"]) + p["fecha_corte"] for p in res.pendientes) if k in abiertos]

        # 5. envio: solo las facturas cuyo corte es el de la corrida, en un album
        cortes_objetivo = {(nic, fc) for nic, cs in objetivos.items() for fc in cs}
        enviables = [d for d in res.descargadas
                     if (d["nic"], d["corte"]) in cortes_objetivo
                     and not db.factura_ya_enviada(conn, d["ref_pago"])]
        fuera = [d for d in res.descargadas if d not in enviables]
        for d in fuera:
            db.log_evento(conn, "aviso", "Factura no enviada: su corte no es el de la corrida",
                          f"NIC {d['nic']} corte {d['corte']}")

        if enviar and enviables:
            texto = _caption(fiso, enviables, res.pendientes)
            try:
                mensajes = telegram.enviar_album([d["path"] for d in enviables],
                                                 caption=texto, chat_id=chat_id)
                res.mensaje_enviado = True
                # file_id de cada documento del album (mismo orden que las facturas)
                detalles = {}
                for d, msg in zip(enviables, mensajes or []):
                    archivo = (msg or {}).get("document") or {}
                    if d.get("id"):
                        detalles[d["id"]] = {"file_id": archivo.get("file_id", ""),
                                             "message_id": (msg or {}).get("message_id")}
                # solo las que de verdad fueron en el album (no todo el dia)
                db.marcar_enviadas(conn, [d["id"] for d in enviables if d.get("id")],
                                   chat_id or config.TELEGRAM_CHAT_ID, detalles=detalles)
                # los PDFs ya estan en Telegram: se borran del servidor
                for d in enviables:
                    _borrar_pdf(Path(d["path"]))
                db.log_evento(conn, "info", f"Enviadas {len(enviables)} facturas a Telegram",
                              f"{len(mensajes)} mensaje(s)")
            except telegram.TelegramError as exc:
                db.log_evento(conn, "error", "Fallo el envio a Telegram", str(exc))
        elif enviar and reporte and not res.pendientes:
            # silencio total: solo se habla por Telegram cuando hay factura nueva
            try:
                telegram.enviar_mensaje(f"Sin facturas con corte {_fecha_legible(fiso)}.",
                                        chat_id=chat_id)
                res.mensaje_enviado = True
            except telegram.TelegramError as exc:
                db.log_evento(conn, "error", "Fallo el envio del reporte", str(exc))

    # 6. historico: guardar (y mandar) las ultimas facturas de cada cliente
    if historico:
        try:
            res.historico = sincronizar_historial(fecha=fecha, enviar=enviar,
                                                  chat_id=chat_id, portales=portales)
        except Exception as exc:  # noqa: BLE001 - el historico no tumba la corrida
            res.historico = {"errores": [str(exc)[:200]], "nuevos": [], "enviados": 0}

    return res




# ------------------------------------------------------------------ historico
def sincronizar_historial(fecha: date | None = None, por_cliente: int | None = None,
                          enviar: bool = True, chat_id: str | None = None,
                          portales: dict | None = None, nic: str | None = None) -> dict:
    """Guarda TODAS las facturas de consumo que el portal conserve de cada cliente.

    `por_cliente` limita cuantas guardar (0 o None = todas).

    Descarga las que falten en la base, las deja en `facturas/` con el mismo
    nombrado y despues las manda a Telegram una sola vez (las ya enviadas no se
    repiten). Devuelve un resumen para el panel y para el log.
    """
    fecha = fecha or date.today()
    res: dict = {"nuevos": [], "errores": [], "enviados": 0, "albumes": 0}
    if portales is None:
        portales = {"edenorte": EdenorteClient()}
        if config.EDESUR_TOKEN or (config.EDESUR_USER and config.EDESUR_PASSWORD):
            portales["edesur"] = EdesurPortal()
        _anotar_portales(portales)

    activos: dict[str, object] = {}
    for nombre_prov, portal in portales.items():
        try:
            portal.login()
            activos[nombre_prov] = portal
        except Exception as exc:  # noqa: BLE001 - sin credenciales o portal caido
            res["errores"].append({"proveedor": nombre_prov, "error": str(exc)[:200]})

    solo_nic = (nic or "").strip()
    with db.sesion() as conn:
        for contrato in db.listar_contratos(conn):
            nic_actual = str(contrato["nic"])
            if solo_nic and nic_actual != solo_nic:
                continue
            nic = nic_actual
            prov = (contrato["proveedor"] if "proveedor" in contrato.keys() else "") or "edenorte"
            portal = activos.get(prov)
            if portal is None:
                continue
            try:
                hist = portal.historial(nic)
            except PortalError as exc:
                db.log_evento(conn, "aviso", f"Historico no disponible para NIC {nic}", str(exc)[:200])
                res["errores"].append({"nic": nic, "error": str(exc)[:200]})
                continue

            # se descargan las que faltan y se reintentan las que quedaron sin PDF:
            # el portal a veces publica el archivo unos dias despues
            faltan = [f for f in hist.filas
                      if es_consumo(f.tipo) and db.necesita_pdf(conn, f.ref_pago)]
            sin_pdf = 0
            tope = len(faltan) if not por_cliente else min(int(por_cliente), len(faltan))
            for fila in faltan[:tope]:
                if sin_pdf >= 2:
                    # dos fallos seguidos: no insistir con las viejas
                    db.log_evento(conn, "aviso",
                                  f"NIC {nic}: no se pudo bajar mas facturas", fila.ref_pago)
                    break
                try:
                    contenido = portal.descargar_pdf(fila.ref_pago)
                    sin_pdf = 0
                except PdfNoDisponible:
                    # El portal LISTA la factura pero no entrega el archivo (Edenorte
                    # solo conserva los PDFs de las mas recientes). Se guarda igual,
                    # marcada 'sin_pdf', para que la ficha muestre todo el historico
                    # del portal; en cada corrida se reintenta por si aparece.
                    db.guardar_factura(conn, {
                        "nic": nic, "ref_pago": fila.ref_pago, "tipo": fila.tipo,
                        "cliente": fila.cliente, "emision": fecha_portal(fila.emision),
                        "estado": "sin_pdf",
                    })
                    db.log_evento(conn, "info",
                                  f"NIC {nic}: factura del portal sin PDF",
                                  f"ref {fila.ref_pago} ({fila.emision}) - queda listada")
                    continue
                except PortalError as exc:
                    sin_pdf += 1
                    res["errores"].append({"nic": nic, "ref": fila.ref_pago, "error": str(exc)[:160]})
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_pdf = Path(tmp) / "f.pdf"
                    tmp_pdf.write_bytes(contenido)
                    try:
                        datos = datos_de_archivo(tmp_pdf, proveedor=prov)
                    except Exception as exc:  # noqa: BLE001
                        res["errores"].append({"nic": nic, "ref": fila.ref_pago,
                                               "error": f"PDF ilegible: {exc}"[:160]})
                        continue

                if datos.corte and datos.corte > fecha.isoformat():
                    continue                                   # factura futura: no aplica
                if datos.kwh is None:
                    db.guardar_factura(conn, {
                        "nic": nic, "ref_pago": fila.ref_pago, "tipo": fila.tipo,
                        "cliente": datos.titular or fila.cliente, "corte": datos.corte,
                        "emision": datos.emision, "vence": datos.vence,
                        "facturado": datos.facturado, "estado": "sin_consumo",
                    })
                    continue

                if not datos.corte:
                    # sin corte la factura no sirve para el panel: se avisa
                    res["errores"].append({"nic": nic, "ref": fila.ref_pago,
                                           "error": "el PDF no trae fecha de corte"})
                    db.log_evento(conn, "aviso",
                                  f"NIC {nic}: factura sin fecha de corte",
                                  f"ref {fila.ref_pago} (revisar con app.reparar)")

                destino = _candidato_archivo(nic, datos, contrato["razon_social"] or "",
                                             config.FACTURAS_DIR)
                _guardar_pdf(contenido, destino)
                db.guardar_factura(conn, {
                    "nic": nic, "ref_pago": fila.ref_pago, "no_factura": datos.no_factura,
                    "tipo": fila.tipo, "cliente": datos.titular or fila.cliente,
                    "periodo_ini": datos.periodo_ini, "periodo_fin": datos.periodo_fin,
                    "corte": datos.corte, "emision": datos.emision, "vence": datos.vence,
                    "dias": datos.dias, "kwh": datos.kwh, "sin_subsidio": datos.sin_subsidio,
                    "subsidio": datos.subsidio, "facturado": datos.facturado,
                    "pdf_path": str(destino), "estado": "descargada",
                })
                if datos.corte:
                    db.cerrar_pendiente(conn, nic, datos.corte)
                if datos.corte and not contrato["dia_corte"]:
                    # el dia de corte se aprende de la propia factura
                    db.upsert_contrato(conn, nic, dia_corte=int(datos.corte[-2:]))
                res["nuevos"].append({
                    "nic": nic, "proveedor": prov, "corte": datos.corte,
                    "facturado": datos.facturado, "kwh": datos.kwh, "path": str(destino),
                })
            guardadas = sum(1 for n in res["nuevos"] if str(n.get("nic")) == nic)
            db.log_evento(conn, "info", f"Historico revisado NIC {nic}",
                          f"{guardadas} guardada(s) de {len(faltan)} disponible(s)")

        # completar el dia de corte de clientes que ya tienen facturas guardadas
        sin_dia = conn.execute(
            """SELECT f.nic, MAX(f.corte) AS corte FROM facturas f
               JOIN contratos c ON c.nic = f.nic
               WHERE (c.dia_corte IS NULL OR c.dia_corte = 0)
                 AND f.corte IS NOT NULL AND f.estado = 'descargada'
               GROUP BY f.nic""").fetchall()
        for fila in sin_dia:
            cortes = [r["corte"] for r in conn.execute(
                "SELECT corte FROM facturas WHERE nic = ? AND corte IS NOT NULL "
                "AND estado = 'descargada' ORDER BY corte DESC LIMIT 6",
                (fila["nic"],)).fetchall()]
            dia = _dia_mas_comun(cortes) or int(fila["corte"][-2:])
            db.upsert_contrato(conn, fila["nic"], dia_corte=dia)
            res.setdefault("dias_corte", []).append({"nic": fila["nic"], "dia": int(fila["corte"][-2:])})

        # envio al grupo: SOLO la ultima factura de cada cliente (una), y solo
        # si esa no se ha mandado antes
        todas = [dict(r) for r in conn.execute(
            """SELECT f.id, f.nic, f.corte, f.enviada, f.pdf_path, f.ref_pago,
                      f.tg_file_id, COALESCE(c.proveedor, 'edenorte') AS proveedor
               FROM facturas f LEFT JOIN contratos c ON c.nic = f.nic
               WHERE f.estado = 'descargada' AND f.corte IS NOT NULL AND f.corte <= ?
               ORDER BY f.corte""", (fecha.isoformat(),))]
        ultima_por_cliente: dict[str, dict] = {}
        for fila in todas:
            actual = ultima_por_cliente.get(fila["nic"])
            if actual is None or (fila["corte"] or "") > (actual["corte"] or ""):
                ultima_por_cliente[fila["nic"]] = fila
        # solo la ultima de cada cliente, y solo si esa ultima no se ha enviado
        a_enviar = sorted((f for f in ultima_por_cliente.values() if not f["enviada"]),
                          key=lambda f: f["corte"] or "")

        if enviar and a_enviar:
            # un mensaje por cliente: su ultima factura, con su propio pie
            for p in a_enviar:
                # ya salio al grupo (marca o file_id): no se repite
                if db.factura_ya_enviada(conn, p["ref_pago"]):
                    db.log_evento(conn, "info",
                                  f"NIC {p['nic']}: la factura de {p['corte']} ya estaba enviada")
                    continue

                portal = activos.get(p.get("proveedor") or "edenorte")
                ruta = _pdf_para_enviar(conn, p, portal)
                if ruta is None:
                    db.log_evento(conn, "error",
                                  f"NIC {p['nic']}: no se encontro el PDF de la factura",
                                  f"corte {p['corte']} ref {p['ref_pago']}")
                    res["errores"].append({"nic": p["nic"],
                                           "envio": "el PDF no esta en el servidor"})
                    continue

                texto = _caption(fecha.isoformat(), [
                    {"nic": p["nic"], "razon_social": _razon_social(conn, p["nic"]),
                     "alias": _campo(conn, p["nic"], "alias"),
                     "asesor": _campo(conn, p["nic"], "asesor"),
                     "corte": p["corte"], "vence": _vence(conn, p["nic"], p["corte"]),
                     "facturado": _monto(conn, p["nic"], p["corte"])}], [])
                try:
                    respuesta = telegram.enviar_documento(ruta, caption=texto, chat_id=chat_id)
                    archivo = (respuesta or {}).get("document") or {}
                    db.marcar_enviadas(conn, [p["id"]],
                                       chat_id or config.TELEGRAM_CHAT_ID,
                                       detalles={p["id"]: {
                                           "file_id": archivo.get("file_id", ""),
                                           "message_id": (respuesta or {}).get("message_id")}})
                    res["enviados"] += 1
                    res["albumes"] += 1
                    db.log_evento(conn, "info",
                                  f"NIC {p['nic']}: factura de {p['corte']} enviada",
                                  f"ref {p['ref_pago']}")
                    # se borra el archivo del servidor: queda el file_id de Telegram
                    _borrar_pdf(ruta)
                except telegram.TelegramError as exc:
                    db.log_evento(conn, "error", f"No se pudo enviar la factura de {p['nic']}",
                                  str(exc)[:300])
                    res["errores"].append({"nic": p["nic"], "envio": str(exc)[:200]})
            if res["enviados"]:
                db.log_evento(conn, "info",
                              f"Enviada la ultima factura de {res['enviados']} cliente(s)")

    return res


def contrato_dia_corte(conn, nic: str) -> int | None:
    """Dia de corte que ya tiene el contrato (None si no se sabe todavia)."""
    fila = conn.execute("SELECT dia_corte FROM contratos WHERE nic = ?", (str(nic),)).fetchone()
    return (fila["dia_corte"] if fila and fila["dia_corte"] else None) if fila else None


def _dia_mas_comun(cortes: list[str]) -> int | None:
    """Dia de corte mas repetido (el corte real varia +-1 dia entre meses)."""
    from collections import Counter

    dias = [int(c[-2:]) for c in cortes if c and len(c) >= 10 and c[-2:].isdigit()]
    if not dias:
        return None
    return Counter(dias).most_common(1)[0][0]


def _nombre_pdf(guardada: str | None) -> str:
    """Nombre del archivo aunque la ruta guardada sea de otra plataforma."""
    texto = str(guardada or "")
    if not texto:
        return ""
    return PureWindowsPath(texto).name if "\\" in texto else PurePosixPath(texto).name


def _fijar_pdf_path(conn, factura_id: int, ruta: Path) -> None:
    conn.execute("UPDATE facturas SET pdf_path = ? WHERE id = ?", (str(ruta), factura_id))
    conn.commit()


def _pdf_para_enviar(conn, fila, portal=None) -> Path | None:
    """PDF listo para mandar: en su ruta, en facturas/ o bajandolo de nuevo.

    La base es compartida entre Windows y WSL: una ruta `C:\\...` no existe en el
    servidor (y al reves), y antes eso hacia que la factura se saltara en silencio.
    """
    guardada = str(fila["pdf_path"] or "")
    if guardada and Path(guardada).exists():
        return Path(guardada)

    nombre = _nombre_pdf(guardada)
    if nombre:
        candidata = Path(config.FACTURAS_DIR) / nombre
        if candidata.exists():
            _fijar_pdf_path(conn, fila["id"], candidata)
            return candidata

    if portal is not None:
        try:
            contenido = portal.descargar_pdf(fila["ref_pago"])
        except Exception as exc:  # noqa: BLE001 - sin PDF no se puede mandar
            db.log_evento(conn, "aviso",
                          f"NIC {fila['nic']}: no se pudo bajar el PDF para enviar",
                          f"ref {fila['ref_pago']}: {str(exc)[:120]}")
            return None
        destino = Path(config.FACTURAS_DIR) / (nombre or f"{fila['nic']} {fila['ref_pago']}.pdf")
        _guardar_pdf(contenido, destino)
        _fijar_pdf_path(conn, fila["id"], destino)
        db.log_evento(conn, "info",
                      f"NIC {fila['nic']}: PDF descargado en el servidor para enviarlo",
                      f"ref {fila['ref_pago']}")
        return destino
    return None


def _borrar_pdf(ruta: Path) -> None:
    """Borra el archivo despues de enviarlo (queda el file_id de Telegram)."""
    try:
        Path(ruta).unlink(missing_ok=True)
    except OSError:
        pass


def _valor(fila):
    """Primer valor de una fila sin importar el motor (dict o sqlite3.Row)."""
    if not fila:
        return None
    try:
        return fila["valor"]
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return fila[0]          # sqlite3.Row por posicion
    except (KeyError, IndexError, TypeError):
        return None


def _campo(conn, nic: str, columna: str) -> str:
    fila = conn.execute(f"SELECT {columna} AS valor FROM contratos WHERE nic = ?",
                        (str(nic),)).fetchone()
    valor = _valor(fila)
    return str(valor) if valor not in (None, "") else ""


def _razon_social(conn, nic: str) -> str:
    return _campo(conn, nic, "razon_social")


def _fila_factura(conn, nic: str, corte: str | None, columna: str):
    """Una columna de la factura de ese corte (o la ultima si el corte es NULL).

    Se evita `corte IS ?`: Postgres no puede deducir el tipo del parametro NULL.
    """
    sql = (f"SELECT {columna} AS valor FROM facturas WHERE nic = ? AND "
           "{cond} AND estado = 'descargada' ORDER BY id DESC")
    if corte is None:
        fila = conn.execute(sql.format(cond="corte IS NULL"), (str(nic),)).fetchone()
    else:
        fila = conn.execute(sql.format(cond="corte = ?"), (str(nic), str(corte))).fetchone()
    return _valor(fila)


def _monto(conn, nic: str, corte: str | None) -> float | None:
    valor = _fila_factura(conn, nic, corte, "facturado")
    try:
        return float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _vence(conn, nic: str, corte: str | None) -> str | None:
    valor = _fila_factura(conn, nic, corte, "vence")
    return str(valor) if valor not in (None, "") else None




def _envolver_con_lock(fn):
    """Una sola corrida a la vez. Si ya hay otra, devuelve una respuesta vacia."""
    import functools

    @functools.wraps(fn)
    def envoltura(*args, **kwargs):
        with _exclusivo() as libre:
            if libre:
                return fn(*args, **kwargs)
            with db.sesion() as conn:
                db.log_evento(conn, "aviso",
                              f"Ya hay una corrida en curso; se omite {fn.__name__}")
            if fn.__name__ == "corrida":
                r = Resultado(fecha=(kwargs.get("fecha") or date.today()).isoformat())
                r.historico = {"ocupado": True, "nuevos": [], "enviados": 0, "errores": []}
                return r
            return {"nuevos": [], "errores": [{"error": "ya hay una corrida en curso"}],
                    "enviados": 0, "albumes": 0}
    return envoltura


corrida = _con_cierre_de_sesiones(_envolver_con_lock(corrida))      # noqa: F811
sincronizar_historial = _con_cierre_de_sesiones(
    _envolver_con_lock(sincronizar_historial))                      # noqa: F811


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Corrida diaria de facturas (Edenorte y Edesur)")
    ap.add_argument("--fecha", help="AAAA-MM-DD (por defecto hoy)")
    ap.add_argument("--sin-envio", action="store_true", help="no enviar a Telegram")
    ap.add_argument("--reporte", action="store_true", help="enviar resumen aunque no haya facturas")
    ap.add_argument("--historial", action="store_true",
                    help="solo sincronizar las ultimas facturas (historico) y enviarlas")
    ap.add_argument("--por-cliente", type=int, default=0,
                    help="cuantas facturas guardar por cliente (0 = todas, es el defecto)")
    args = ap.parse_args()

    fecha = datetime.strptime(args.fecha, "%Y-%m-%d").date() if args.fecha else None
    if args.historial:
        salida = sincronizar_historial(fecha=fecha, por_cliente=args.por_cliente,
                                       enviar=not args.sin_envio)
        print(json.dumps(salida, ensure_ascii=False, indent=2))
        # para que systemd/tareas vean el fallo del portal
        sys.exit(1 if salida.get("portal_error") else 0)

    r = corrida(fecha=fecha, enviar=not args.sin_envio, reporte=args.reporte)
    print(json.dumps(r.como_dict(), ensure_ascii=False, indent=2))
    # codigo != 0 si el portal fallo: asi el servicio queda en failed y se ve
    sys.exit(1 if r.portal_error else 0)
