"""Pruebas del cliente y del lector de facturas de Edesur (sin red)."""
from __future__ import annotations

import base64
from datetime import date

import pytest

from app import edesur, pdfdata
from app.edesur import EdesurClient, EdesurError, FacturaEdesur, fecha_de_bill_id
from app.naming import nombre_archivo

# Fragmento real de una factura de Edesur (texto extraido con pdftotext -layout)
TEXTO = """
DUPLICADO

 RNC: 1-01-82124-8

 e-NCF: E320015934293 Factura Consumo Electrónica

 PUNTO DE EMISION:1570- BANI DIRECCIÓN: CALLE DUARTE 42 345-00

 CALLE JUAN PABLO DUARTE #6 Oficina: 1570

 TELÉFONO: 809-522-1844 Finca: Ruta: 11

 REFERENCIADE PAGO: 2115589328-18 SUMIN.: Itiner.: 0003

 FECHADE EMISIÓN: 18/08/2026 REF.: LDO DER N°42A NIC: 2115589

 LOC.: MATANZAS NIS: 2115589

 ORTIZ MARTINEZ, WILLIAN MARIA20223224 Sección: ZONA URBANA Medidor: 15606198

 RNC - CEDULA: 003-0038760-2

 FECHALÍMITE DE PAGO: 17/09/2026 Este es su número de contrato

 PERIODO DE FACTURACIÓN

 NIS: 2115589 2.121 kW 15/07/2026 - 13/08/2026 = 29 días

 TARIFA: BTS1

 Activa B.T. 6,553 6,553 1.0000 0 kWh Cargo fijo
 05/26 369 IMPORTE TOTAL RD$ 42.10
 TITULAR DEL CONTRATO IMPORTE TOTAL SIN SUBSIDIO RD$ 64.70
 CALLE DUARTE IMPORTE EN RD$ 42.10
"""


def test_fecha_de_bill_id():
    assert fecha_de_bill_id("2115589102026081300") == date(2026, 8, 13)
    assert fecha_de_bill_id("6428300062026111000") == date(2026, 11, 10)
    assert fecha_de_bill_id("basura") is None
    assert fecha_de_bill_id("") is None


def test_parsear_edesur_datos_principales():
    d = pdfdata.parsear_edesur(TEXTO)
    assert d.nic == "2115589"
    assert d.titular == "ORTIZ MARTINEZ, WILLIAN MARIA"
    assert d.no_factura == "E320015934293"
    assert d.periodo_ini == "2026-07-15"
    assert d.periodo_fin == "2026-08-13"
    assert d.corte == "2026-08-13"      # el corte es el fin del periodo
    assert d.dias == 29
    assert d.emision == "2026-08-18"
    assert d.vence == "2026-09-17"
    assert d.kwh == 0.0                 # consumo impreso en cero
    assert d.facturado == pytest.approx(42.10)
    assert d.sin_subsidio == pytest.approx(64.70)
    assert d.tarifa == "BTS1"
    assert d.medidor == "15606198"
    assert d.referencia == "2115589328-18"


def test_nombre_archivo_edesur():
    # Edesur imprime APELLIDOS, NOMBRES igual que Edenorte
    assert nombre_archivo("ORTIZ MARTINEZ, WILLIAN MARIA", "2115589") == \
        "WILLIAN MARIA ORTIZ MARTINEZ 2115589.pdf"
    # razon social sin coma se deja tal cual
    assert nombre_archivo("SJG SRL , TRANSPORTE Y AGREGADOS", "7355297") == \
        "TRANSPORTE Y AGREGADOS SJG SRL 7355297.pdf"


def test_detectar_proveedor():
    assert pdfdata.detectar_proveedor(TEXTO) == "edesur"
    assert pdfdata.detectar_proveedor("EDENORTE DOMINICANA S.A.") == "edenorte"


class _Respuesta:
    def __init__(self, datos=None, status=200, texto=None):
        self._datos = datos
        self.status_code = status
        self.content = b"{}" if datos is not None else b""
        self.text = texto if texto is not None else ""

    def json(self):
        return self._datos


class _FalsoSesion:
    """Simula la API de Edesur con la forma real de las respuestas."""

    def __init__(self, contratos, por_mes):
        self.contratos = contratos
        self.por_mes = por_mes
        self.headers = {}
        self.validaciones = []

    def get(self, url, params=None, headers=None, timeout=None):
        if url.endswith("GetClientContracts"):
            return _Respuesta(self.contratos)
        if url.endswith("GetContractBilling"):
            return _Respuesta([])                      # contrato recien asociado
        if url.endswith("GetAvabledBills"):
            return _Respuesta(self.por_mes.get(params["mes"], []))
        if url.endswith("GetBillpdf"):
            return _Respuesta(base64.b64encode(b"%PDF-1.4 falso").decode())
        return _Respuesta({}, 404, "no encontrado")

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        if url.endswith("ValidateContractSGC"):
            self.validaciones.append(json["billref"])
            if json["billref"].endswith("318"):
                return _Respuesta({"contractNo": json["contractid"], "Alias": "x",
                                   "Titular": "ORTIZ MARTINEZ, WILLIAN MARIA"})
            return _Respuesta("Los datos no coinciden", 400)
        if url.endswith("SaveContract"):
            return _Respuesta({"ok": True})
        return _Respuesta({}, 404)


def _cliente_falso(contratos=()):
    por_mes = {8: [{"ID": "2115589102026081300", "Date": "8", "Month": 8}],
               11: [{"ID": "2115589092025111300", "Date": "11", "Month": 11}]}
    cli = EdesurClient(token="token-de-prueba")
    cli.sesion = _FalsoSesion(list(contratos), por_mes)
    return cli


def test_factura_mas_reciente_ignora_la_del_ano_pasado():
    cli = _cliente_falso()
    factura = cli.factura_mas_reciente("2115589", meses_atras=6)
    assert factura is not None
    assert factura.fecha == date(2026, 8, 13)
    assert factura.bill_id == "2115589102026081300"


def test_agregar_contrato_prueba_referencias_cercanas():
    cli = _cliente_falso()
    datos = cli.agregar_contrato("2115589", "2115589310", rango=20)
    assert datos["Titular"] == "ORTIZ MARTINEZ, WILLIAN MARIA"
    # 310 falla, 311...318 -> la valida es 318
    assert cli.sesion.validaciones[-1] == "2115589318"


def test_agregar_contrato_ya_asociado_no_valida():
    cli = _cliente_falso([{"contractNo": "2115589", "Titular": "X"}])
    assert cli.agregar_contrato("2115589")["ya_asociado"] is True
    assert cli.sesion.validaciones == []


def test_pdf_devuelve_bytes():
    cli = _cliente_falso()
    assert cli.pdf(FacturaEdesur("2115589", "2115589102026081300", date(2026, 8, 13),
                                 None, None, "Recibos de consumos", "")).startswith(b"%PDF")


def test_error_si_no_hay_credenciales(monkeypatch):
    monkeypatch.setattr(edesur.config, "EDESUR_USER", "")
    monkeypatch.setattr(edesur.config, "EDESUR_PASSWORD", "")
    monkeypatch.setattr(edesur.config, "EDESUR_TOKEN", "")
    with pytest.raises(EdesurError):
        EdesurClient().login()
