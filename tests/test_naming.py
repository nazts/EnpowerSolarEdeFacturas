import pytest
from app import naming

PROHIBIDOS = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']


@pytest.mark.parametrize("razon,nic,esperado", [
    ("GARCIA CASTRO, FRANCISCO ALBERT", "7533709", "FRANCISCO ALBERT GARCIA CASTRO 7533709.pdf"),
    ("DE LA CRUZ TAVAREZ, JORGE RAMON", "6997885", "JORGE RAMON DE LA CRUZ TAVAREZ 6997885.pdf"),
    ("ENPOWER SOLAR SRL", "1234567", "ENPOWER SOLAR SRL 1234567.pdf"),
    ("  garcia   castro ,  francisco  ", "1", "FRANCISCO GARCIA CASTRO 1.pdf"),
])
def test_nombre_archivo(razon, nic, esperado):
    assert naming.nombre_archivo(razon, nic) == esperado


def test_sin_coma_se_deja_tal_cual():
    assert naming.normalizar_razon_social("ENPOWER SOLAR SRL") == "ENPOWER SOLAR SRL"


def test_vacio():
    assert naming.normalizar_razon_social("") == ""


def test_caracteres_prohibidos():
    n = naming.nombre_archivo("EMPRESA A/B: SRL", "55")
    assert n == "EMPRESA A-B- SRL 55.pdf"
    for c in PROHIBIDOS:
        assert c not in n


@pytest.mark.parametrize("c", PROHIBIDOS)
def test_cada_caracter_prohibido(c):
    assert c not in naming.nombre_archivo(f"AB{c}CD", "9")


def test_nunca_ref_pago_en_nombre():
    n = naming.nombre_archivo("DE LA CRUZ TAVAREZ, JORGE RAMON", "6997885")
    assert "6997885044" not in n
    assert n.endswith(" 6997885.pdf")


def test_runner_usa_nic_y_no_ref_pago(tmp_path):
    from app import runner
    from app.pdfdata import DatosFactura
    d = DatosFactura(titular="X, Y", corte="2026-09-16")
    p = runner._candidato_archivo("6997885", d, "DE LA CRUZ TAVAREZ, JORGE RAMON", tmp_path)
    assert p.name == "JORGE RAMON DE LA CRUZ TAVAREZ 6997885.pdf"
    assert "6997885044" not in p.name
