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


FACTURA_CON_PERIODO = """No. Factura..................: 202611033807
Ref. Pago......................: 6501165082
FECHA EMISION..........:20/10/2026
NOMBRE O RAZON SOCIAL: PAULA YVELISES GONZALEZ GARCIA
No. De D{acento}as Facturados : 20/09/2026 - 20/10/2026 = 30 dias
Activa B.T.  06042264    417    452    1.00    550kWh    Cargo Fijo
550 kWh X RD$10.23        RD$   5,629.31
IMPORTE SIN SUBSIDIO EN RD$      RD$    5,629.31
FACTURADO MES OCT     RD$5,629.31"""


def test_el_corte_sale_aunque_el_texto_llegue_mal_codificado():
    """pdftotext en Linux escupe UTF-8; leido como latin-1, "Días" queda "DÃ­as".

    Con eso se perdian corte y periodo: el cliente se quedaba sin dia de corte.
    """
    from app.pdfdata import parsear

    for acento in ("í", "Ã­", "i"):        # correcto, mojibake y sin acento
        d = parsear(FACTURA_CON_PERIODO.format(acento=acento))
        assert d.corte == "2026-10-20", f"fallo con {acento!r}"
        assert d.periodo_ini == "2026-09-20"
        assert d.periodo_fin == "2026-10-20"
        assert d.dias == 30


def test_sin_etiqueta_de_periodo_el_corte_es_el_fin_del_periodo():
    from app.pdfdata import parsear

    texto = FACTURA_CON_PERIODO.format(acento="í").replace(
        "No. De Días Facturados : 20/09/2026 - 20/10/2026 = 30 dias", "")
    d = parsear(texto)
    assert d.corte is None or d.corte == d.periodo_fin


def test_periodo_con_formato_alternativo():
    from app.pdfdata import parsear

    texto = FACTURA_CON_PERIODO.format(acento="í").replace(
        "No. De Días Facturados : 20/09/2026 - 20/10/2026 = 30 dias",
        "PERIODO DE FACTURACION: 20/09/2026 al 20/10/2026")
    d = parsear(texto)
    assert d.corte == "2026-10-20"
