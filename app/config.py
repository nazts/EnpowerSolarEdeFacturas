"""Configuracion del proyecto: lee .env y expone constantes."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env(path: Path = BASE_DIR / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


_load_env()

EDENORTE_URL = os.environ.get("EDENORTE_URL", "https://ofv.edenorte.com.do").rstrip("/")
EDENORTE_EMAIL = os.environ.get("EDENORTE_EMAIL", "")
EDENORTE_PASSWORD = os.environ.get("EDENORTE_PASSWORD", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DB_PATH = BASE_DIR / os.environ.get("DB_PATH", "data/enpower.db")
FACTURAS_DIR = BASE_DIR / os.environ.get("FACTURAS_DIR", "facturas")
EXPORT_DIR = BASE_DIR / os.environ.get("EXPORT_DIR", "exports")


def guardar_chat_id(chat_id: str) -> None:
    """Persiste TELEGRAM_CHAT_ID en .env y lo actualiza en memoria."""
    global TELEGRAM_CHAT_ID
    chat_id = str(chat_id).strip()
    ruta = BASE_DIR / ".env"
    lineas = ruta.read_text(encoding="utf-8").splitlines() if ruta.exists() else []
    salida, encontrado = [], False
    for linea in lineas:
        if linea.strip().startswith("TELEGRAM_CHAT_ID="):
            salida.append(f"TELEGRAM_CHAT_ID={chat_id}")
            encontrado = True
        else:
            salida.append(linea)
    if not encontrado:
        salida.append(f"TELEGRAM_CHAT_ID={chat_id}")
    ruta.write_text("\n".join(salida) + "\n", encoding="utf-8")
    TELEGRAM_CHAT_ID = chat_id
    os.environ["TELEGRAM_CHAT_ID"] = chat_id
