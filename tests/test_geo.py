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

# --------------------------------------------- las tres vistas del mapa
def test_el_mapa_ofrece_las_tres_vistas_de_google_maps():
    """Mapa / Satélite / Relieve, como en Google Maps."""
    from pathlib import Path

    plantilla = (Path(__file__).resolve().parent.parent
                 / "app" / "templates" / "mapa.html").read_text(encoding="utf-8")
    for vista in ("calles", "satelite", "relieve"):
        assert f'data-estilo="{vista}"' in plantilla, vista
    assert '<span>Mapa</span>' in plantilla
    assert '<span>Satélite</span>' in plantilla
    assert '<span>Relieve</span>' in plantilla
    assert 'aria-pressed="true"' in plantilla


def test_las_tres_capas_son_de_esri_y_tienen_atribucion():
    """OpenStreetMap/OpenTopoMap bloquean las apps internas: se usa Esri."""
    from pathlib import Path

    plantilla = (Path(__file__).resolve().parent.parent
                 / "app" / "templates" / "mapa.html").read_text(encoding="utf-8")
    assert plantilla.count("server.arcgisonline.com") == 3
    assert "World_Street_Map" in plantilla          # Mapa
    assert "World_Imagery" in plantilla             # Satélite
    assert "World_Topo_Map" in plantilla            # Relieve
    for capa in ("World_Street_Map", "World_Imagery", "World_Topo_Map"):
        tramo = plantilla.split(capa, 1)[1][:260]
        assert "attr:" in tramo, f"la capa {capa} no tiene atribución"


def test_la_vista_elegida_se_recuerda():
    """Sin navegador no se puede simular localStorage; se comprueba el contrato."""
    from pathlib import Path

    plantilla = (Path(__file__).resolve().parent.parent
                 / "app" / "templates" / "mapa.html").read_text(encoding="utf-8")
    assert "localStorage.setItem('mapa-estilo'" in plantilla
    assert "localStorage.getItem('mapa-estilo')" in plantilla
