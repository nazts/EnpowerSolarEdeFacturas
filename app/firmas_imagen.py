"""Prepara la imagen de una firma o sello antes de guardarla.

Lo que se sube suele ser un escaneo o una foto del sello: fondo blanco opaco que,
al pegarlo en el PDF, taparía los totales, la marca de agua o el pie. Acá se le
quita ese fondo **sólo por fuera** (rellenando desde los bordes), así un sello con
blanco en el centro no se perfora, y se recorta el aire sobrante para que la firma
se pueda ubicar justa donde va.
"""
from __future__ import annotations

import io
from collections import deque
from typing import Any

from PIL import Image

UMBRAL_BLANCO = 232      # desde este valor (0-255) se considera fondo
MARGEN = 4               # píxeles que se dejan alrededor al recortar


def _es_fondo(pixel: tuple[int, int, int, int], umbral: int) -> bool:
    r, g, b, a = pixel
    if a == 0:
        return True
    return r >= umbral and g >= umbral and b >= umbral


def _quitar_fondo(imagen: Image.Image, umbral: int = UMBRAL_BLANCO) -> Image.Image:
    """Vuelve transparente el fondo blanco conectado con el borde de la imagen."""
    ancho, alto = imagen.size
    pixeles: Any = imagen.load()          # PixelAccess: se indexa por (x, y)
    visto = bytearray(ancho * alto)
    cola: deque[tuple[int, int]] = deque()

    for x in range(ancho):
        cola.append((x, 0))
        cola.append((x, alto - 1))
    for y in range(alto):
        cola.append((0, y))
        cola.append((ancho - 1, y))

    while cola:
        x, y = cola.popleft()
        if x < 0 or y < 0 or x >= ancho or y >= alto:
            continue
        indice = y * ancho + x
        if visto[indice]:
            continue
        if not _es_fondo(pixeles[x, y], umbral):
            continue
        visto[indice] = 1
        pixeles[x, y] = (255, 255, 255, 0)
        cola.append((x + 1, y))
        cola.append((x - 1, y))
        cola.append((x, y + 1))
        cola.append((x, y - 1))
    return imagen


def preparar_firma(datos: bytes, umbral: int = UMBRAL_BLANCO) -> bytes:
    """Devuelve un PNG con transparencia, recortado al contenido.

    - Si la imagen ya trae transparencia real (PNG con alfa), se respeta.
    - Si no, se le quita el fondo blanco que toca los bordes.
    - Siempre se recorta el aire de alrededor y se guarda como PNG.
    """
    original = Image.open(io.BytesIO(datos))
    original.load()
    tiene_alfa = original.mode in ("RGBA", "LA") or "transparency" in original.info
    imagen = original.convert("RGBA")

    if tiene_alfa:
        alfa = imagen.getchannel("A")
        if alfa.getextrema()[0] == 255:      # alfa todo opaco: es lo mismo que no tener
            tiene_alfa = False
    if not tiene_alfa:
        imagen = _quitar_fondo(imagen, umbral)

    caja = imagen.getbbox()                  # recorta lo transparente de alrededor
    if caja:
        izquierda, arriba, derecha, abajo = caja
        izquierda = max(izquierda - MARGEN, 0)
        arriba = max(arriba - MARGEN, 0)
        derecha = min(derecha + MARGEN, imagen.width)
        abajo = min(abajo + MARGEN, imagen.height)
        if derecha - izquierda >= 8 and abajo - arriba >= 8:
            imagen = imagen.crop((izquierda, arriba, derecha, abajo))

    salida = io.BytesIO()
    imagen.save(salida, format="PNG", optimize=True)
    return salida.getvalue()
