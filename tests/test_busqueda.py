"""Buscador del modulo de clientes (sin acentos, por palabras, varios campos)."""
from app.dashboard import _coincide, _normalizar_busqueda

CLIENTE = {
    "nic": "6997885",
    "razon_social": "DE LA CRUZ TAVAREZ, JORGE RAMON",
    "nombre_normalizado": "JORGE RAMON DE LA CRUZ TAVAREZ",
    "alias": "TALLER VALLE VERDE",
    "asesor": "Luis",
    "direccion": "Calle Duarte 12",
    "telefono": "809-258-1309",
    "proveedor": "edenorte",
}


def test_normaliza_mayusculas_y_acentos():
    assert _normalizar_busqueda("JOSÉ") == "jose"
    assert _normalizar_busqueda("ÑANDÚ") == "nandu"
    assert _normalizar_busqueda(None) == ""


def test_encuentra_por_nic_nombre_alias_asesor_direccion_y_telefono():
    for termino in ("6997885", "cruz", "taller", "luis", "duarte", "258"):
        assert _coincide(CLIENTE, [termino]), termino


def test_varias_palabras_tienen_que_aparecer_todas():
    assert _coincide(CLIENTE, ["jorge", "tavarez"])
    assert not _coincide(CLIENTE, ["jorge", "edesur"])


def test_no_confunde_acentos_al_buscar():
    cliente = dict(CLIENTE, razon_social="JOSÉ RAMÓN ÑÁÑEZ")
    assert _coincide(cliente, ["jose"])
    assert _coincide(cliente, ["nanez"])


def test_busqueda_que_no_coincide():
    assert not _coincide(CLIENTE, ["zzz"])
