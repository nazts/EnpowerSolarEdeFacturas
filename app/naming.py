"""Normalizacion de la razon social y nombre del archivo de cada factura.

Regla del cliente: el PDF no se modifica nunca; solo cambia el NOMBRE.
Edenorte imprime `APELLIDOS, NOMBRES` y el archivo debe quedar
`NOMBRES APELLIDOS <NIC>.pdf`.
"""
from __future__ import annotations

import re
import unicodedata

# Caracteres que Windows no permite en un nombre de archivo
PROHIBIDOS = r'\\/:*?"<>|'
SEPARADOR = " "          # entre nombre normalizado y NIC
CONSERVAR_TILDES = True  # False => translitera a ASCII


def normalizar_razon_social(valor: str) -> str:
    """'GARCIA CASTRO, FRANCISCO ALBERT' -> 'FRANCISCO ALBERT GARCIA CASTRO'."""
    if not valor:
        return ""
    texto = re.sub(r"\s+", " ", valor).strip().strip(",")
    if "," in texto:
        apellidos, _, nombres = texto.partition(",")
        texto = f"{nombres.strip()} {apellidos.strip()}"
    texto = re.sub(r"\s+", " ", texto).strip()
    texto = texto.upper()
    if not CONSERVAR_TILDES:
        texto = unicodedata.normalize("NFKD", texto)
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        texto = texto.replace("Ñ", "N")
    return texto


def limpiar_nombre_archivo(nombre: str) -> str:
    """Quita caracteres prohibidos y espacios repetidos."""
    limpio = "".join("-" if c in PROHIBIDOS else c for c in nombre)
    limpio = re.sub(r"\s+", " ", limpio).strip(" .-")
    return limpio


def nombre_archivo(razon_social: str, nic: str) -> str:
    """Nombre final: '<NOMBRE NORMALIZADO><SEP><NIC>.pdf'."""
    base = limpiar_nombre_archivo(normalizar_razon_social(razon_social))
    nic = str(nic).strip()
    return f"{base}{SEPARADOR}{nic}.pdf"


def nombre_mostrado(razon_social: str, alias: str = "") -> str:
    """Para reportes/Telegram: alias del pagador si existe, si no la razon social."""
    alias = (alias or "").strip()
    if alias:
        return alias.upper()
    return normalizar_razon_social(razon_social)
