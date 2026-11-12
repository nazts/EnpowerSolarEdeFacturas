"""Enlace de mapa que se comparte con el cliente (Google Maps)."""
from app.geo import enlace_mapa


def test_enlace_mapa_es_el_de_google_con_las_coordenadas():
    url = enlace_mapa(19.402583, -70.727444)
    assert url == "https://www.google.com/maps/search/?api=1&query=19.402583,-70.727444"


def test_enlace_mapa_acepta_negativos_y_decimales_cortos():
    assert enlace_mapa(-0.5, 12) == "https://www.google.com/maps/search/?api=1&query=-0.5,12"


def test_no_queda_ningun_enlace_a_openstreetmap_en_el_codigo():
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent / "app"
    culpables = []
    for archivo in list(raiz.rglob("*.html")) + list(raiz.rglob("*.py")):
        texto = archivo.read_text(encoding="utf-8", errors="ignore")
        if "openstreetmap.org/?mlat" in texto:
            culpables.append(archivo.name)
    assert not culpables, f"quedan enlaces viejos a OSM en: {culpables}"
