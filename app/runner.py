"""Corrida diaria: revisa contratos, descarga las facturas que cortan hoy y las envia."""
from __future__ import annotations

import calendar
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import config, db, naming, telegram
from .edenorte import EdenorteClient, PortalError
from .pdfdata import DatosFactura, datos_de_archivo

TIPO_CONSUMO = "consumo"
ALERTA_PORTAL = "La plataforma de Edenorte tiene problemas"


# ------------------------------------------------------------------ utilidades
def corta_hoy(dia_corte: int | None, fecha: date) -> bool:
    """True si el contrato tiene su corte en la fecha dada (mes corto => ultimo dia)."""
    if not dia_corte:
        return False
    ultimo = calendar.monthrange(fecha.year, fecha.month)[1]
    esperado = min(int(dia_corte), ultimo)
    return fecha.day == esperado


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

    def como_dict(self) -> dict:
        return {
            "fecha": self.fecha,
            "descargadas": self.descargadas,
            "pendientes": self.pendientes,
            "descartadas": self.descartadas,
            "portal_error": self.portal_error,
            "mensaje_enviado": self.mensaje_enviado,
        }


def _guardar_pdf(origen: bytes, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".part")
    tmp.write_bytes(origen)
    tmp.replace(destino)
    return destino


def _candidato_archivo(nic: str, datos: DatosFactura, razon_social: str,
                       carpeta: Path) -> Path:
    nombre = naming.nombre_archivo(razon_social or (datos.titular or ""), nic)
    destino = carpeta / nombre
    if destino.exists():
        # ya existe uno con ese nombre y distinto contenido -> sufijo con el corte
        destino = carpeta / f"{destino.stem} ({datos.corte or 'sin-corte'}).pdf"
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
        lineas = [f"NIC {f['nic']} · {nombre}"]
        monto = f"{f['facturado']:,.2f}" if f.get("facturado") is not None else "s/d"
        lineas.append(f"Pagar: RD${monto}")
        if f.get("asesor"):
            lineas.append(f"Asesor: {f['asesor']}")
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
            reporte: bool = False) -> Resultado:
    fecha = fecha or date.today()
    fiso = fecha.isoformat()
    res = Resultado(fecha=fiso)
    portal = cliente or EdenorteClient()

    with db.sesion() as conn:
        # 1. sesion
        try:
            portal.login()
        except PortalError as exc:
            res.portal_error = str(exc)
            db.log_evento(conn, "error", ALERTA_PORTAL, str(exc))
            if enviar:
                try:
                    telegram.enviar_mensaje(
                        f"⚠️ <b>{ALERTA_PORTAL}</b>\nNo se pudo iniciar sesión el {fiso}."
                        f"\nDetalle: {str(exc)[:300]}", chat_id=chat_id)
                    res.mensaje_enviado = True
                except telegram.TelegramError as terr:
                    db.log_evento(conn, "error", "No se pudo enviar la alerta a Telegram", str(terr))
            return res

        # 2. sincronizar contratos del portal
        try:
            for c in portal.contratos():
                existente = db.obtener_contrato(conn, c.nic)
                if existente is None:
                    db.upsert_contrato(conn, c.nic, razon_social=c.titular)
                elif not existente["razon_social"] and c.titular:
                    db.upsert_contrato(conn, c.nic, razon_social=c.titular)
        except PortalError as exc:
            db.log_evento(conn, "aviso", "No se pudieron listar los contratos", str(exc))

        # 3. objetivos: pendientes abiertos + contratos que cortan hoy
        objetivos: dict[str, set[str]] = {}
        for p in db.pendientes_abiertos(conn):
            objetivos.setdefault(str(p["nic"]), set()).add(p["fecha_corte"])
        for c in db.listar_contratos(conn):
            if corta_hoy(c["dia_corte"], fecha):
                objetivos.setdefault(str(c["nic"]), set()).add(fiso)

        if not objetivos:
            db.log_evento(conn, "info", f"Sin cortes ni pendientes para {fiso}")
            if reporte and enviar:
                telegram.enviar_mensaje(f"Sin facturas con corte {fiso}.", chat_id=chat_id)
                res.mensaje_enviado = True
            return res

        # 4. revisar cada contrato objetivo
        for nic, cortes in objetivos.items():
            contrato = db.obtener_contrato(conn, nic)
            razon_social = contrato["razon_social"] if contrato else ""
            try:
                hist = portal.historial(nic)
            except PortalError as exc:
                db.log_evento(conn, "error", f"Historial no disponible para NIC {nic}", str(exc))
                for fc in cortes:
                    db.registrar_pendiente(conn, nic, fc, f"historial: {exc}")
                continue

            candidatas = [f for f in hist.filas
                          if es_consumo(f.tipo) and not db.factura_existe(conn, f.ref_pago)]
            encontrado_por_corte: set[str] = set()

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
                        datos = datos_de_archivo(tmp_pdf)
                    except Exception as exc:
                        db.log_evento(conn, "error", f"No se pudo leer el PDF {fila.ref_pago}", str(exc))
                        continue

                if datos.corte not in cortes:
                    res.descartadas.append({"nic": nic, "ref_pago": fila.ref_pago,
                                            "corte": datos.corte, "motivo": "corte distinto"})
                    db.guardar_factura(conn, {
                        "nic": nic, "ref_pago": fila.ref_pago, "tipo": fila.tipo,
                        "cliente": datos.titular or fila.cliente,
                        "periodo_ini": datos.periodo_ini, "periodo_fin": datos.periodo_fin,
                        "corte": datos.corte, "emision": datos.emision, "vence": datos.vence,
                        "kwh": datos.kwh, "facturado": datos.facturado,
                        "estado": "descartada",
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

                db.guardar_factura(conn, {
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
                if datos.corte:
                    db.upsert_contrato(conn, nic,
                                       razon_social=razon_social or (datos.titular or ""),
                                       dia_corte=int(datos.corte[-2:]))
                db.cerrar_pendiente(conn, nic, datos.corte)
                encontrado_por_corte.add(datos.corte)
                res.descargadas.append({
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
                ids = [r["id"] for r in db.facturas_del_dia(conn, fiso)]
                db.marcar_enviadas(conn, ids, chat_id or config.TELEGRAM_CHAT_ID)
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

    return res


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Corrida diaria de facturas Edenorte")
    ap.add_argument("--fecha", help="AAAA-MM-DD (por defecto hoy)")
    ap.add_argument("--sin-envio", action="store_true", help="no enviar a Telegram")
    ap.add_argument("--reporte", action="store_true", help="enviar resumen aunque no haya facturas")
    args = ap.parse_args()

    fecha = datetime.strptime(args.fecha, "%Y-%m-%d").date() if args.fecha else None
    r = corrida(fecha=fecha, enviar=not args.sin_envio, reporte=args.reporte)
    print(json.dumps(r.como_dict(), ensure_ascii=False, indent=2))
