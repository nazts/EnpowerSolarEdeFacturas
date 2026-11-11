import pytest
from app.pdfdata import parsear


def test_factura_normal(texto):
    d = parsear(texto())
    assert d.titular == "DE LA CRUZ TAVAREZ, JORGE RAMON"
    assert d.no_factura == "202610180903"
    assert (d.periodo_ini, d.periodo_fin, d.corte) == ("2026-08-16", "2026-09-16", "2026-09-16")
    assert d.dias == 31
    assert d.emision == "2026-09-16"
    assert d.vence == "2026-10-16"
    assert d.kwh == 35.0
    assert d.sin_subsidio == 629.59
    assert d.subsidio == -380.31
    assert d.facturado == 249.28
    assert d.tarifa == "BTS-1"


def test_kwh_con_separador_de_miles_pegado():
    t = ("No. De Días Facturados : 05/09/2026 - 05/10/2026 = 30 dias\r\n"
         "Activa B.T. 1 2 3 1.00 7,980kWh  Cargo Fijo\r\n")
    d = parsear(t)
    assert d.kwh == 7980.0
    assert d.corte == "2026-10-05"


def test_facturado_en_linea_posterior_con_codigo_de_barras(texto):
    t = texto(total="355473.04", codigo_barras=True)
    assert "FACTURADO MES SEP\r\n" in t
    assert parsear(t).facturado == 355473.04


def test_facturado_misma_linea(texto):
    assert parsear(texto(total_misma_linea=True, total="1,234.50", codigo_barras=False)).facturado == 1234.50


def test_consumo_en_blanco_devuelve_none(texto):
    d = parsear(texto(consumo="", detalle_kwh=False))
    assert d.kwh is None
    assert d.corte == "2026-09-16"


def test_facturado_linea_posterior_sin_codigo_de_barras(texto):
    assert parsear(texto(codigo_barras=False)).facturado == 249.28


def test_texto_vacio():
    d = parsear("")
    assert d.kwh is None and d.corte is None and d.facturado is None
