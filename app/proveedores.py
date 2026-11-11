"""Adaptadores para que Edenorte y Edesur se usen igual desde el runner y el panel.

`EdenorteClient` (scraping con CSRF) y `EdesurClient` (API con token) tienen
interfaces distintas. Aqui Edesur se envuelve con la misma forma que Edenorte
(`login`, `contratos`, `historial`, `descargar_pdf`, `asociar_contrato`) para que
la corrida diaria no tenga que saber de que distribuidora se trata.
"""
from __future__ import annotations

from dataclasses import dataclass

from .edesur import EdesurClient, EdesurError
from .edenorte import Contrato, EdenorteClient, FacturaRef, Historial, PortalError

# Nombres visibles en el panel
NOMBRES = {"edenorte": "Edenorte", "edesur": "Edesur"}
PROVEEDORES = tuple(NOMBRES)
ALERTA = {
    "edenorte": "La plataforma de Edenorte tiene problemas",
    "edesur": "La plataforma de Edesur tiene problemas",
}


def nombre(proveedor: str | None) -> str:
    return NOMBRES.get((proveedor or "edenorte").lower(), "Edenorte")


@dataclass
class EdesurPortal:
    """Edesur con la misma cara que `EdenorteClient`."""

    proveedor: str = "edesur"

    def __init__(self, cliente: EdesurClient | None = None):
        self.cliente = cliente or EdesurClient()
        self.proveedor = "edesur"

    # ---------------------------------------------------------------- sesion
    def login(self) -> None:
        try:
            self.cliente.login()
        except EdesurError as exc:
            texto = str(exc)
            if "credenciales" in texto.lower():
                # sin EDESUR_USER/EDESUR_PASSWORD: no es una falla del portal
                raise PortalError(f"SIN_CREDENCIALES: {texto}") from exc
            raise PortalError(texto) from exc

    def _traducir(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except EdesurError as exc:
            raise PortalError(str(exc)) from exc

    # -------------------------------------------------------------- contratos
    def contratos(self) -> list[Contrato]:
        datos = self._traducir(self.cliente.contratos)
        return [Contrato(nic=str(c.get("contractNo") or "").strip(),
                         titular=str(c.get("Titular") or "").strip(),
                         direccion=str(c.get("dir") or "").strip())
                for c in datos if c.get("contractNo")]

    def titular(self, nic: str) -> str:
        for c in self.contratos():
            if c.nic == str(nic).strip():
                return c.titular
        return ""

    def asociar_contrato(self, nic: str, espera_max: int = 60,
                         referencia: str | None = None) -> Contrato:
        """Asocia el NIC (probando referencias cercanas si hace falta)."""
        datos = self._traducir(self.cliente.agregar_contrato, nic, referencia)
        return Contrato(nic=str(datos.get("contractNo") or nic),
                        titular=str(datos.get("Titular") or "").strip(),
                        direccion=str(datos.get("dir") or "").strip())

    # --------------------------------------------------------------- facturas
    def historial(self, nic: str) -> Historial:
        """Facturas publicadas del contrato, de la mas nueva a la mas vieja."""
        facturas = self._traducir(self.cliente.facturas, nic, 6)
        if not facturas:
            reciente = self._traducir(self.cliente.factura_mas_reciente, nic)
            facturas = [reciente] if reciente else []
        filas = []
        for f in facturas:
            if not f:
                continue
            filas.append(FacturaRef(
                nic=str(nic), ref_pago=f.bill_id, cliente="",
                tipo=f.tipo or "Recibos de consumos",
                emision=f.fecha.isoformat() if f.fecha else "",
            ))
        return Historial(nic=str(nic), filas=filas)

    def descargar_pdf(self, ref_pago: str) -> bytes:
        return self._traducir(self.cliente.pdf, ref_pago)

    def ultimo_corte(self, nic: str) -> int | None:
        """Dia del mes en que corta el contrato, segun su factura mas reciente."""
        reciente = self._traducir(self.cliente.factura_mas_reciente, nic, 5)
        return reciente.fecha.day if reciente and reciente.fecha else None

    # comodidad para el panel
    def referencia_de(self, nic: str) -> str | None:
        reciente = self._traducir(self.cliente.factura_mas_reciente, nic)
        return reciente.referencia if reciente else None


def portal_de(proveedor: str | None):
    """Devuelve el cliente del proveedor indicado (por defecto Edenorte)."""
    if (proveedor or "edenorte").lower() == "edesur":
        return EdesurPortal()
    return EdenorteClient()
