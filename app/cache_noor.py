"""Caché persistente en SQLite local para los datos de la API Noor.

Evita re-consultar la API Noor en cada carga del panel: las respuestas se
guardan en `data/cache_noor.db` y se reutilizan mientras no expiren.

Uso:
    from . import cache_noor
    val = cache_noor.get("plantas", max_edad_seg=300)
    if val is None:
        val = noor.lista_plantas()
        cache_noor.set("plantas", val)
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

CACHE_DB = Path(__file__).resolve().parent.parent / "data" / "cache_noor.db"


def _conectar() -> sqlite3.Connection:
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(CACHE_DB))
    con.execute(
        "CREATE TABLE IF NOT EXISTS cache_noor ("
        "  clave TEXT PRIMARY KEY,"
        "  datos TEXT NOT NULL,"
        "  ts    REAL NOT NULL"
        ")"
    )
    return con


def get(clave: str, max_edad_seg: float):
    """Devuelve el dato cacheado si es más reciente que max_edad_seg; si no, None."""
    try:
        con = _conectar()
        fila = con.execute(
            "SELECT datos, ts FROM cache_noor WHERE clave = ?", (clave,)
        ).fetchone()
        con.close()
        if fila and (time.time() - fila[1]) < max_edad_seg:
            return json.loads(fila[0])
    except Exception:  # noqa: BLE001 - el caché nunca debe romper el flujo
        pass
    return None


def set(clave: str, datos) -> None:
    """Guarda `datos` (serializable a JSON) bajo `clave`."""
    try:
        con = _conectar()
        con.execute(
            "INSERT OR REPLACE INTO cache_noor (clave, datos, ts) VALUES (?, ?, ?)",
            (clave, json.dumps(datos, ensure_ascii=False, default=str), time.time()),
        )
        con.commit()
        con.close()
    except Exception:  # noqa: BLE001
        pass
