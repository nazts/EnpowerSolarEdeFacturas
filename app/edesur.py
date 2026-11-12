"""Cliente de la Oficina Virtual de Edesur (ov.edesur.com.do).

A diferencia de Edenorte (HTML con formularios y CSRF), Edesur es una SPA
Angular que habla con su propia API: `https://ovapi.edesur.com.do/ovapi`
autenticada con un token Bearer. Aqui se usa la API directo, sin navegador.

Flujo comprobado contra el portal real:
  getToken -> Contracts/GetClientContracts -> (ValidateContractSGC +
  SaveContract para asociar un NIC) -> Contract/GetContractBilling ->
  IndraComplaints/GetBillpdf  (devuelve el PDF en base64)
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

import requests

from . import config

API = config.EDESUR_API.rstrip("/")

# Tipos de factura que si se descargan (igual que en Edenorte)
TIPO_RECIBO = "Recibos de consumos"


class EdesurError(Exception):
    """Falla del portal de Edesur (login, API o factura)."""


@dataclass
class FacturaEdesur:
    """Una factura tal como la publica la API de Edesur."""

    nic: str
    bill_id: str
    fecha: date | None          # fecha de la factura = fecha de corte
    total: float | None
    vence: date | None
    tipo: str
    estado: str
    referencia: str | None = None

    @property
    def es_recibo(self) -> bool:
        return (self.tipo or "").strip().lower() == TIPO_RECIBO.lower()

    @property
    def corte_iso(self) -> str | None:
        return self.fecha.isoformat() if self.fecha else None


def fecha_de_bill_id(bill_id: str) -> date | None:
    """La API no siempre manda la fecha; el id la lleva dentro.

    billId = <NIS 7 digitos><secuencia 2><AAAAMMDD><00>
    p.ej. 6428300 06 20261110 00  ->  2026-11-10
    """
    m = re.search(r"(\d{4})(\d{2})(\d{2})\d{2}$", str(bill_id or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _fecha(valor: str | None) -> date | None:
    if not valor:
        return None
    texto = str(valor).strip().split(" ")[0]
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


class EdesurClient:
    """Sesion autenticada contra la API de Edesur."""

    def __init__(self, usuario: str | None = None, password: str | None = None,
                 token: str | None = None, timeout: int = 60):
        self.usuario = usuario or config.EDESUR_USER
        self.password = password or config.EDESUR_PASSWORD
        self.token = token or config.EDESUR_TOKEN or None
        self.timeout = timeout
        self.sesion = requests.Session()
        self.sesion.headers.update({"Accept": "application/json"})

    def cerrar(self) -> None:
        """Suelta el token y cierra la conexion (la API no tiene logout)."""
        self.token = None
        try:
            self.sesion.close()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ login
    def login(self) -> str:
        if self.token and not (self.usuario and self.password):
            return self.token          # ya hay token (p.ej. EDESUR_TOKEN en .env)
        if not self.usuario or not self.password:
            raise EdesurError(
                "Faltan las credenciales de Edesur (EDESUR_USER / EDESUR_PASSWORD en .env)")
        try:
            r = self.sesion.post(
                f"{API}/getToken",
                data={"grant_type": "password", "username": self.usuario,
                      "password": self.password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise EdesurError(f"No se pudo conectar con Edesur: {exc}") from exc
        if r.status_code >= 400:
            raise EdesurError(f"Edesur rechazo el inicio de sesion (HTTP {r.status_code})")
        datos = r.json() if r.content else {}
        self.token = datos.get("access_token")
        if not self.token:
            raise EdesurError("Edesur no devolvio el token de acceso")
        return self.token

    # ------------------------------------------------------------------ API
    def _get(self, ruta: str, **params) -> requests.Response:
        if not self.token:
            self.login()
        try:
            return self.sesion.get(f"{API}/{ruta.lstrip('/')}",
                                   params=params or None, headers=self._cabeceras(),
                                   timeout=self.timeout)
        except requests.RequestException as exc:
            raise EdesurError(f"Fallo de red en {ruta}: {exc}") from exc

    def _post(self, ruta: str, cuerpo: dict) -> requests.Response:
        if not self.token:
            self.login()
        try:
            return self.sesion.post(f"{API}/{ruta.lstrip('/')}", json=cuerpo,
                                    headers=self._cabeceras(), timeout=self.timeout)
        except requests.RequestException as exc:
            raise EdesurError(f"Fallo de red en {ruta}: {exc}") from exc

    def _cabeceras(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _json(self, r, que: str):
        if r.status_code == 401:
            # token vencido: renovar una sola vez
            self.login()
            raise EdesurError(f"Token vencido al pedir {que}")
        if r.status_code >= 400:
            detalle = ""
            try:
                cuerpo = r.json()
                detalle = cuerpo.get("Message") if isinstance(cuerpo, dict) else str(cuerpo)
            except ValueError:
                detalle = r.text
            raise EdesurError(f"Edesur respondio {r.status_code} en {que}: "
                              f"{(detalle or '')[:250]}")
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError as exc:
            raise EdesurError(f"Respuesta invalida de Edesur en {que}") from exc

    # -------------------------------------------------------------- contratos
    def contratos(self) -> list[dict]:
        """Todos los contratos asociados a la cuenta."""
        return self._json(self._get("Contracts/GetClientContracts"), "GetClientContracts") or []

    def tiene_contrato(self, nic: str) -> bool:
        nic = str(nic).strip()
        return any(str(c.get("contractNo")) == nic for c in self.contratos())

    def validar_contrato(self, nic: str, referencia: str, alias: str = "x") -> dict:
        """Valida NIC + referencia de pago. Devuelve los datos con el titular real."""
        cuerpo = {"contractid": int(str(nic).strip()),
                  "billref": str(referencia).strip(),
                  "alias": (alias or "x")[:1]}
        return self._json(self._post("Contracts/ValidateContractSGC", cuerpo),
                          "ValidateContractSGC")

    def asociar_contrato(self, nic: str, referencia: str, alias: str = "x") -> dict:
        """Valida y asocia el contrato a la cuenta (equivalente al boton del portal)."""
        datos = self.validar_contrato(nic, referencia, alias)
        if not datos:
            raise EdesurError(f"Edesur no devolvio datos para el NIC {nic}")
        self._json(self._post("Contracts/SaveContract", datos), "SaveContract")
        return datos

    def agregar_contrato(self, nic: str, referencia: str | None = None,
                         alias: str = "x", rango: int = 50) -> dict:
        """Asegura que el NIC este en la cuenta; si no, lo asocia.

        Los tres digitos de la referencia suben de uno en uno con cada factura,
        asi que si la referencia dada ya no vale se prueba en un rango cercano
        (hacia adelante primero: la factura nueva siempre tiene la referencia mayor).
        """
        nic = str(nic).strip()
        if self.tiene_contrato(nic):
            return {"contractNo": nic, "ya_asociado": True}

        base: int | None = None
        if referencia:
            try:
                return self.asociar_contrato(nic, referencia, alias)
            except EdesurError as exc:
                if "ya esta asociado" in str(exc).lower():
                    return {"contractNo": nic, "ya_asociado": True}
                sufijo = str(referencia).strip()
                sufijo = sufijo[len(nic):] if sufijo.startswith(nic) else sufijo
                if sufijo.isdigit():
                    base = int(sufijo)
                ultimo_error = exc
        else:
            ultimo_error = EdesurError("sin referencia de pago")

        if base is None:
            raise EdesurError(
                f"El NIC {nic} no esta asociado y no hay referencia valida para probar "
                f"({ultimo_error})")

        # adelante (facturas nuevas) y luego atras, sin salir de 000-999
        candidatos = [n for n in range(base + 1, min(base + rango, 999) + 1)]
        candidatos += [n for n in range(base - 1, max(base - rango, 0) - 1, -1)]
        for tres in candidatos:
            candidato = f"{nic}{tres:03d}"
            try:
                return self.asociar_contrato(nic, candidato, alias)
            except EdesurError as exc:
                ultimo_error = exc
                if "ya esta asociado" in str(exc).lower():
                    return {"contractNo": nic, "ya_asociado": True}
        raise EdesurError(f"No se pudo asociar el NIC {nic} en +-{rango} referencias: {ultimo_error}")

    # --------------------------------------------------------------- facturas
    def facturas(self, nic: str, largo: int = 6) -> list[FacturaEdesur]:
        """Facturas recientes del contrato (manda la fecha y el monto)."""
        datos = self._json(self._get("Contract/GetContractBilling",
                                     contractid=str(nic).strip(), length=largo),
                           "GetContractBilling") or []
        salida = []
        for f in datos:
            salida.append(FacturaEdesur(
                nic=str(nic).strip(),
                bill_id=str(f.get("billId") or ""),
                fecha=_fecha(f.get("billDate")) or fecha_de_bill_id(f.get("billId")),
                total=f.get("totalAmount"),
                vence=_fecha(f.get("expirationDate")),
                tipo=f.get("billTypeDescription") or "",
                estado=f.get("billStatusDescription") or "",
                referencia=str(f.get("simbolo_var") or "") or None,
            ))
        return salida

    def facturas_por_mes(self, nic: str, mes: int) -> list[dict]:
        """Facturas publicadas para un mes concreto (1..12)."""
        return self._json(self._get("IndraComplaints/GetAvabledBills",
                                    contractid=str(nic).strip(), mes=mes),
                          "GetAvabledBills") or []

    def factura_mas_reciente(self, nic: str, solo_recibos: bool = True,
                             meses_atras: int = 8) -> FacturaEdesur | None:
        """La factura mas nueva publicada.

        No basta con `GetContractBilling` (los contratos recien asociados lo
        devuelven vacio): se revisan tambien los ultimos meses con
        `GetAvabledBills`, que trae facturas de años anteriores cuando el mes
        pedido aun no tiene la del año en curso.
        """
        nic = str(nic).strip()
        candidatas: list[FacturaEdesur] = [f for f in self.facturas(nic, largo=12) if f.fecha]

        hoy = date.today()
        meses: list[tuple[int, int]] = []
        for atras in range(meses_atras + 1):
            total = hoy.year * 12 + (hoy.month - 1) - atras
            meses.append((total // 12, total % 12 + 1))
        for anio, mes in meses:
            for f in self.facturas_por_mes(nic, mes):
                bill_id = str(f.get("ID") or "")
                fecha = fecha_de_bill_id(bill_id)
                if not fecha or fecha.year != anio:      # descarta la de otro año
                    continue
                candidatas.append(FacturaEdesur(
                    nic=nic, bill_id=bill_id, fecha=fecha, total=None, vence=None,
                    tipo=TIPO_RECIBO, estado=str(f.get("Status") or "")))

        if solo_recibos:
            filtradas = [f for f in candidatas if f.es_recibo]
            candidatas = filtradas or candidatas
        if not candidatas:
            return None
        return max(candidatas, key=lambda f: (f.fecha or date.min, f.bill_id))

    def pdf(self, factura: FacturaEdesur | str) -> bytes:
        """PDF de la factura (la API lo entrega como base64). Nunca se modifica."""
        bill_id = factura.bill_id if isinstance(factura, FacturaEdesur) else str(factura)
        r = self._get("IndraComplaints/GetBillpdf", facRef=bill_id)
        if r.status_code >= 400:
            raise EdesurError(f"Edesur respondio {r.status_code} al pedir el PDF de {bill_id}")
        try:
            b64 = r.json()
        except ValueError:
            b64 = r.text
        b64 = b64.strip().strip('"')
        try:
            return base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            raise EdesurError(f"El PDF de {bill_id} no vino en base64 valido: {exc}") from exc


# --------------------------------------------------------------------- tarea
def descargar_ultima(nic: str, referencia: str | None = None, alias: str = "x",
                     cliente: "EdesurClient | None" = None) -> dict:
    """Descarga la factura mas reciente de un NIC, la renombra y devuelve el resumen.

    No modifica el PDF: solo se guarda con el nombre `NOMBRES APELLIDOS <NIC>.pdf`.
    """
    from .naming import nombre_archivo
    from .pdfdata import datos_de_archivo

    cli = cliente or EdesurClient()
    nic = str(nic).strip()
    if not cli.tiene_contrato(nic):
        cli.agregar_contrato(nic, referencia, alias)

    factura = cli.factura_mas_reciente(nic)
    if not factura:
        raise EdesurError(f"El contrato {nic} no tiene facturas publicadas")

    pdf = cli.pdf(factura)
    config.FACTURAS_DIR.mkdir(parents=True, exist_ok=True)
    temporal = config.FACTURAS_DIR / f"_tmp_{nic}.pdf"
    temporal.write_bytes(pdf)

    info = datos_de_archivo(temporal, proveedor="edesur")
    titular = info.titular or ""
    if not titular:
        titular = next((c.get("Titular") or "" for c in cli.contratos()
                        if str(c.get("contractNo")) == nic), "")
    destino = config.FACTURAS_DIR / nombre_archivo(titular, nic)
    temporal.replace(destino)

    return {
        "nic": nic,
        "titular": titular,
        "archivo": str(destino),
        "bytes": destino.stat().st_size,
        "bill_id": factura.bill_id,
        "corte": info.corte or factura.corte_iso,
        "periodo_ini": info.periodo_ini,
        "periodo_fin": info.periodo_fin,
        "dias": info.dias,
        "kwh": info.kwh,
        "facturado": info.facturado if info.facturado is not None else factura.total,
        "sin_subsidio": info.sin_subsidio,
        "subsidio": info.subsidio,
        "vence": info.vence,
        "tarifa": info.tarifa,
        "referencia": info.referencia,
        "tipo_factura": factura.tipo,
    }


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    p = argparse.ArgumentParser(description="Descarga la factura mas reciente de Edesur")
    p.add_argument("--nic", nargs="+", required=True, help="NIC o varios")
    p.add_argument("--ref", help="referencia de pago (sin el guion) si el contrato no esta asociado")
    p.add_argument("--alias", default="x", help="alias provisional solo si hay que asociarlo")
    args = p.parse_args(argv)

    cliente = EdesurClient()
    for nic in args.nic:
        try:
            print(_json.dumps(descargar_ultima(nic, args.ref, args.alias, cliente),
                              ensure_ascii=False, indent=2))
        except EdesurError as exc:
            print(f"NIC {nic}: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
