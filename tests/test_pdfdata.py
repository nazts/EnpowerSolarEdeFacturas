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


def test_sin_periodo_no_inventa_una_fecha_de_corte():
    """Sin la linea del periodo no hay corte: mejor vacio que uno equivocado."""
    from app.pdfdata import parsear

    texto = FACTURA_CON_PERIODO.format(acento="í").replace(
        "No. De Días Facturados : 20/09/2026 - 20/10/2026 = 30 dias", "")
    d = parsear(texto)
    assert d.corte is None and d.periodo_fin is None


def test_periodo_con_formato_alternativo():
    from app.pdfdata import parsear

    texto = FACTURA_CON_PERIODO.format(acento="í").replace(
        "No. De Días Facturados : 20/09/2026 - 20/10/2026 = 30 dias",
        "PERIODO DE FACTURACION: 20/09/2026 al 20/10/2026")
    d = parsear(texto)
    assert d.corte == "2026-10-20"


def test_variantes_del_periodo():
    """Formatos que aparecen en los PDFs reales (y en los de Edesur)."""
    from app.pdfdata import _periodo

    casos = {
        "No. De Días Facturados : 20/09/2026 - 20/10/2026 = 30 dias": ("2026-09-20", "2026-10-20", 30),
        "No. De DÃ­as Facturados : 20/09/2026 - 20/10/2026 = 30 dias": ("2026-09-20", "2026-10-20", 30),
        "No. De Días Facturados : 2/9/2026 - 5/10/2026 = 33 dias": ("2026-09-02", "2026-10-05", 33),
        "No. De Días Facturados : 02-09-2026 - 05-10-2026 = 33 dias": ("2026-09-02", "2026-10-05", 33),
        "No. De Días Facturados : 02/09/26 - 05/10/26 = 33 dias": ("2026-09-02", "2026-10-05", 33),
        "No. De Días Facturados : 20/09/2026 – 20/10/2026 = 30 dias": ("2026-09-20", "2026-10-20", 30),
        "NO. DE DIAS FACTURADOS : 20/09/2026 - 20/10/2026 = 30 dias": ("2026-09-20", "2026-10-20", 30),
        "PERIODO DE FACTURACIÓN 20/09/2026 al 20/10/2026": ("2026-09-20", "2026-10-20", None),
        # Edesur: la etiqueta arriba y las fechas abajo, con el NIS en medio
        " PERIODO DE FACTURACIÓN\n\n NIS: 2115589 2.121 kW 15/07/2026 - 13/08/2026 = 29 días":
            ("2026-07-15", "2026-08-13", 29),
        # sin etiqueta, pero con "= N dias" (formato de Edesur)
        " NIS: 2115589 15/07/2026 - 16/09/2026 = 29 días": ("2026-07-15", "2026-09-16", 29),
    }
    for texto, esperado in casos.items():
        assert _periodo(texto) == esperado, texto


def test_dos_periodos_se_queda_con_el_mas_nuevo():
    from app.pdfdata import _periodo

    texto = ("No. De Días Facturados : 20/08/2026 - 20/09/2026 = 31 dias\n"
             "No. De Días Facturados : 20/09/2026 - 20/10/2026 = 30 dias")
    assert _periodo(texto)[:2] == ("2026-09-20", "2026-10-20")


def test_no_confunde_el_periodo_con_la_fecha_de_emision():
    """Dos fechas sueltas despues de la etiqueta no son el periodo."""
    from app.pdfdata import _periodo

    texto = "Días Facturados\nFECHA EMISION: 20/10/2026 - 30/10/2026"
    assert _periodo(texto) == (None, None, None)


def test_texto_pdf_decodifica_utf8(monkeypatch):
    """El bug de origen: pdftotext en Linux emite UTF-8 y se leia como latin-1."""
    import subprocess

    from app import pdfdata

    class Salida:
        stdout = "No. De Días Facturados : 20/09/2026 - 20/10/2026 = 30 dias".encode("utf-8")

    monkeypatch.setattr(pdfdata, "_binario", lambda: "pdftotext")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Salida())

    texto = pdfdata.texto_pdf("cualquier.pdf")

    assert "Días" in texto                      # sin mojibake
    assert pdfdata.parsear(texto).corte == "2026-10-20"
