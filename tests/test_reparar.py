"""Reparacion de facturas guardadas sin fecha de corte."""
import pathlib

import pytest

from app import config, db, pdfdata, reparar

TEXTO = """No. De Días Facturados : 20/09/2026 - 20/10/2026 = 30 dias
FECHA EMISION..........:20/10/2026
NOMBRE O RAZON SOCIAL: PAULA YVELISES GONZALEZ GARCIA
550 kWh X RD$10.23        RD$   5,629.31
IMPORTE SIN SUB.SIDIO EN RD$      RD$    5,629.31
FACTURADO MES OCT     RD$5,629.31"""


@pytest.fixture
def una_factura_sin_corte(db_path, tmp_path, monkeypatch):
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(b"PDF simulado")
    with db.sesion(db_path=db_path) as c:
        db.upsert_contrato(c, "6501165", razon_social="PAULA YVELISES")
        c.execute("INSERT INTO facturas (nic, ref_pago, tipo, corte, emision, kwh, "
                  "facturado, pdf_path, estado) VALUES (?,?,?,?,?,?,?,?,?)",
                  ("6501165", "6501165082", "Recibos de consumos", None,
                   "2026-10-20", 550.0, 5629.31, str(pdf), "descargada"))
        c.commit()
    return db_path


def test_reparar_completa_el_corte_y_aprende_el_dia(una_factura_sin_corte, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(reparar, "datos_de_archivo",
                        lambda p, proveedor="edenorte": pdfdata.parsear(TEXTO))

    r = reparar.reparar()

    assert r["revisadas"] == 1
    assert r["corregidas"] == 1
    with db.sesion(db_path=una_factura_sin_corte) as c:
        f = c.execute("SELECT corte, periodo_fin FROM facturas WHERE ref_pago = '6501165082'"
                      ).fetchone()
        k = c.execute("SELECT dia_corte FROM contratos WHERE nic = '6501165'").fetchone()
    assert f["corte"] == "2026-10-20"
    assert f["periodo_fin"] == "2026-10-20"
    assert k["dia_corte"] == 20


def test_reparar_en_simulacion_no_escribe(una_factura_sin_corte, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(reparar, "datos_de_archivo",
                        lambda p, proveedor="edenorte": pdfdata.parsear(TEXTO))

    r = reparar.reparar(simular=True)

    assert r["corregidas"] == 1          # lo detecta
    with db.sesion(db_path=una_factura_sin_corte) as c:
        assert c.execute("SELECT corte FROM facturas WHERE ref_pago = '6501165082'"
                         ).fetchone()["corte"] is None    # pero no lo escribe


def test_reparar_salta_las_que_no_tienen_pdf(una_factura_sin_corte, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    with db.sesion(db_path=una_factura_sin_corte) as c:
        c.execute("UPDATE facturas SET pdf_path = '/no/existe.pdf'")
        c.commit()

    r = reparar.reparar()

    assert r["revisadas"] == 1 and r["sin_pdf"] == 1 and r["corregidas"] == 0
