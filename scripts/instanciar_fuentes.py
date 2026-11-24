"""Genera las instancias estáticas de Archivo y Genos para el PDF de cotización.

Las descargas de Google Fonts son variables (`Archivo[wdth,wght].ttf`): fpdf2
necesita archivos estáticos, así que se fijan los ejes con fontTools.
"""
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

TMP = Path(r"C:\Users\Luis Martinez\AppData\Local\Temp\fuentes_var")
SALIDA = Path(__file__).resolve().parent.parent / "app" / "static" / "fonts" / "pdf"
SALIDA.mkdir(parents=True, exist_ok=True)

TRABAJOS = [
    ("Archivo.ttf", {"wght": 400, "wdth": 100}, "Archivo-Regular.ttf"),
    ("Archivo.ttf", {"wght": 600, "wdth": 100}, "Archivo-SemiBold.ttf"),
    ("Genos.ttf", {"wght": 300}, "Genos-Light.ttf"),
    ("Genos.ttf", {"wght": 600}, "Genos-SemiBold.ttf"),
]

for origen, ejes, nombre in TRABAJOS:
    fuente = TTFont(TMP / origen)
    instancia = instantiateVariableFont(fuente, ejes, updateFontNames=True)
    destino = SALIDA / nombre
    instancia.save(destino)
    print(f"{nombre}: {destino.stat().st_size} bytes")
