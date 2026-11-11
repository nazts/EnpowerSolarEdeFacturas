"""Pruebas del adaptador que hace que Edesur se vea como Edenorte."""
from __future__ import annotations

from datetime import date

from app.edesur import FacturaEdesur
from app.proveedores import EdesurPortal, nombre, portal_de


class _FalsoClienteEdesur:
    """Imita solo lo que usa el adaptador."""

    def login(self):
        return "token"

    def contratos(self):
        return [{"contractNo": "2115589", "Titular": "ORTIZ MARTINEZ, WILLIAN MARIA",
                 "dir": "CALLE DUARTE #42"}]

    def facturas(self, nic, largo=6):
        return [FacturaEdesur(nic=nic, bill_id="2115589102026081300",
                              fecha=date(2026, 8, 13), total=42.10, vence=None,
                              tipo="Recibos de consumos", estado="", referencia="2115589328")]

    def factura_mas_reciente(self, nic, **kwargs):
        return self.facturas(nic)[0]

    def pdf(self, ref):
        return b"%PDF-1.4"

    def agregar_contrato(self, nic, referencia=None):
        return {"contractNo": nic, "Titular": "ORTIZ MARTINEZ, WILLIAN MARIA"}


def test_nombre_visible():
    assert nombre("edenorte") == "Edenorte"
    assert nombre("edesur") == "Edesur"
    assert nombre(None) == "Edenorte"
    assert nombre("DESCONOCIDO") == "Edenorte"


def test_portal_de_devuelve_el_cliente_correcto():
    assert EdesurPortal(cliente=_FalsoClienteEdesur()).proveedor == "edesur"
    assert portal_de("edesur").__class__.__name__ == "EdesurPortal"
    assert portal_de("edenorte").__class__.__name__ == "EdenorteClient"


def test_contratos_adaptados():
    portal = EdesurPortal(cliente=_FalsoClienteEdesur())
    contratos = portal.contratos()
    assert contratos[0].nic == "2115589"
    assert contratos[0].titular == "ORTIZ MARTINEZ, WILLIAN MARIA"
    assert portal.titular("2115589") == "ORTIZ MARTINEZ, WILLIAN MARIA"


def test_historial_usa_bill_id_como_referencia():
    portal = EdesurPortal(cliente=_FalsoClienteEdesur())
    hist = portal.historial("2115589")
    assert len(hist.filas) == 1
    fila = hist.filas[0]
    assert fila.ref_pago == "2115589102026081300"   # el runner lo usa como referencia unica
    assert "consumo" in fila.tipo.lower()           # es_consumo() del runner lo acepta
    assert fila.emision == "2026-08-13"


def test_asociar_y_pdf():
    portal = EdesurPortal(cliente=_FalsoClienteEdesur())
    contrato = portal.asociar_contrato("2115589", referencia="2115589310")
    assert contrato.nic == "2115589"
    assert contrato.titular == "ORTIZ MARTINEZ, WILLIAN MARIA"
    assert portal.descargar_pdf("2115589102026081300").startswith(b"%PDF")
