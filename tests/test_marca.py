"""Identidad de marca (Manual de Marca 2026) aplicada al panel y a los PDF.

Colores oficiales: verde #26412D, dorado #BB894C, crema #FAF3E9.
Tipografía: Archivo (títulos, subtítulos y cuerpo) y Genos (cifras), servidas
desde `app/static/fonts` — el panel es interno y no puede depender de un CDN.
"""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PLANTILLAS = RAIZ / "app" / "templates"
FUENTES = RAIZ / "app" / "static" / "fonts"


def _lum(hexc):
    hexc = hexc.lstrip("#")
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contraste(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# --------------------------------------------------------------- colores
def test_los_tres_colores_oficiales_estan_en_la_plantilla():
    base = (PLANTILLAS / "base.html").read_text(encoding="utf-8")
    assert "#FAF3E9" in base          # crema de fondo
    assert "#26412D" in base          # verde principal
    assert "#BB894C" in base          # dorado de acento


def test_no_quedan_colores_de_la_paleta_vieja():
    viejos = ("#F5F2E9", "#2E4435", "#EEE9DB", "#FCFAF5", "#B8874A", "#D9A566")
    for archivo in list(PLANTILLAS.glob("*.html")) + list((RAIZ / "app").glob("*.py")):
        texto = archivo.read_text(encoding="utf-8")
        for hexc in viejos:
            assert hexc not in texto, f"{archivo.name} todavía usa {hexc}"


def test_el_contraste_del_texto_cumple_aa():
    assert _contraste("#26412D", "#FAF3E9") >= 4.5      # texto sobre fondo
    assert _contraste("#556B58", "#FAF3E9") >= 4.5      # secundario sobre fondo
    assert _contraste("#FAF3E9", "#26412D") >= 4.5      # crema sobre verde
    assert _contraste("#FAF3E9", "#101B12") >= 4.5      # oscuro: crema sobre verde profundo


def test_el_dorado_no_se_usa_como_texto_sobre_crema():
    """El manual lo da como acento: sobre crema no llega a 4.5."""
    assert _contraste("#BB894C", "#FAF3E9") < 4.5


# ------------------------------------------------------------ tipografía
def test_base_declara_las_dos_familias_y_sus_tokens():
    base = (PLANTILLAS / "base.html").read_text(encoding="utf-8")
    assert base.count("@font-face") >= 4
    assert "font-family:'Archivo'" in base
    assert "font-family:'Genos'" in base
    assert "--font-txt:'Archivo'" in base
    assert "--font-num:'Genos'" in base
    assert "font-family:var(--font-txt)" in base


def test_las_cifras_grandes_usan_genos():
    base = (PLANTILLAS / "base.html").read_text(encoding="utf-8")
    assert re.search(r"\.val,\.stat \.v[^{]*\{[^}]*var\(--font-num\)", base, re.S)


def test_el_login_tambien_carga_las_fuentes():
    login = (PLANTILLAS / "login.html").read_text(encoding="utf-8")
    assert "@font-face" in login and "--font-txt" in login


def test_los_archivos_de_fuente_estan_en_el_repo():
    woff2 = sorted(FUENTES.glob("*.woff2"))
    assert len(woff2) == 4, [f.name for f in woff2]
    assert all(f.stat().st_size > 10_000 for f in woff2)
    assert {f.name for f in woff2} == {"archivo-latin.woff2", "archivo-latin-ext.woff2",
                                       "genos-latin.woff2", "genos-latin-ext.woff2"}


# -------------------------------------------------------------------- PDF
def test_la_cotizacion_usa_la_paleta_y_las_fuentes_de_marca():
    codigo = (RAIZ / "app" / "cotizacion_pdf.py").read_text(encoding="utf-8")
    assert "VERDE = (38, 65, 45)" in codigo
    assert "DORADO = (187, 137, 76)" in codigo
    assert "CREMA = (250, 243, 233)" in codigo
    assert "Archivo-Regular.ttf" in codigo and "Genos-Light.ttf" in codigo
    assert "Helvetica" not in codigo


def test_las_fuentes_del_pdf_existen():
    ttf = sorted(p.name for p in (FUENTES / "pdf").glob("*.ttf"))
    assert ttf == ["Archivo-Regular.ttf", "Archivo-SemiBold.ttf",
                   "Genos-Light.ttf", "Genos-SemiBold.ttf"], ttf
