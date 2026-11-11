"""Geocodificacion con OpenStreetMap / Nominatim (1 peticion por segundo)."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

UA = "EnpowerSolarFacturas/1.0 (contacto: sometimiento@enpowersolarrd.com)"
URL = "https://nominatim.openstreetmap.org/search"
_ultima = [0.0]


def _esperar() -> None:
    """Nominatim exige maximo 1 peticion por segundo."""
    delta = time.time() - _ultima[0]
    if delta < 1.1:
        time.sleep(1.1 - delta)
    _ultima[0] = time.time()


def geocodificar(direccion: str, contexto: str = "Santiago, República Dominicana") -> dict:
    """Devuelve {ok, lat, lon, display_name} o {ok: False, error}."""
    consulta = ", ".join(p for p in (direccion.strip(), contexto.strip()) if p)
    if not direccion.strip():
        return {"ok": False, "error": "Dirección vacía"}
    _esperar()
    params = urllib.parse.urlencode({
        "q": consulta, "format": "jsonv2", "limit": 1, "addressdetails": 1,
    })
    peticion = urllib.request.Request(f"{URL}?{params}", headers={
        "User-Agent": UA, "Accept-Language": "es",
    })
    try:
        with urllib.request.urlopen(peticion, timeout=25) as respuesta:
            datos = json.loads(respuesta.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"No se pudo consultar el mapa: {exc}"}
    if not datos:
        return {"ok": False, "error": "No se encontró la dirección en el mapa"}
    primero = datos[0]
    return {
        "ok": True,
        "lat": float(primero["lat"]),
        "lon": float(primero["lon"]),
        "display_name": primero.get("display_name", consulta),
    }
