"""Cliente de la API de Noor / Weco (monitoreo solar de las plantas).

Login + token JWT, lista de plantas, energía en vivo, alarmas, dispositivos y
firmware. No usa el teléfono: habla directo con noor.olpess-api.com.

El cifrado AES y la firma MD5 que usa la app SOLO aplican a los endpoints de
firmware OTA (open.*); la API principal va en claro con el token (verificado).
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from . import config

BASE = "https://noor.olpess-api.com"

_token: str | None = None
_token_ts: float = 0.0
_TOKEN_TTL = 3600.0  # el token dura ~1 h; se renueva solo


def _operationtime() -> str:
    """Formato que espera el header Operationtime: YYYY-MM-DD HH:MM:SS(UTC±HH:MM)."""
    now = datetime.now(timezone.utc).astimezone()
    off = now.utcoffset() or timedelta(0)
    signo = "+" if off >= timedelta(0) else "-"
    total = int(abs(off.total_seconds()))
    hh, mm = total // 3600, (total % 3600) // 60
    return now.strftime("%Y-%m-%d %H:%M:%S") + f"(UTC{signo}{hh:02d}:{mm:02d})"


def _headers(token: str | None = None) -> dict:
    h = {
        "Logintool": "Phone",
        "System": "Android 16",
        "Accept-Language": "es",
        "Operationtime": _operationtime(),
        "User-Agent": "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36",
    }
    if token:
        h["Authorization"] = token
    return h


def login(forzar: bool = False) -> str:
    """Devuelve el token JWT, renovándolo si venció. Lanza si falla."""
    global _token, _token_ts
    if _token and not forzar and time.time() - _token_ts < _TOKEN_TTL:
        return _token
    if not config.NOOR_USUARIO or not config.NOOR_CLAVE:
        raise RuntimeError("Faltan NOOR_USUARIO / NOOR_CLAVE en el .env")
    r = requests.post(BASE + "/api/login", data={
        "email": config.NOOR_USUARIO, "password": config.NOOR_CLAVE,
        "phoneOSType": "Android 16", "system": "Android 16",
        "loginTime": _operationtime(), "mobileId": uuid.uuid4().hex,
        "encryption": "false",
    }, headers=_headers(), timeout=25)
    r.raise_for_status()
    cuerpo = r.json()
    if not cuerpo.get("success"):
        raise RuntimeError("Login Noor falló: " + str(cuerpo.get("errMessage") or cuerpo))
    _token = cuerpo["data"]["token"]
    _token_ts = time.time()
    return _token  # type: ignore[return-value]


def _post(path: str, body: dict | None = None) -> dict:
    r = requests.post(BASE + path, json=body or {}, headers=_headers(login()), timeout=30)
    r.raise_for_status()
    return r.json()


def _get(path: str, params: dict | None = None) -> dict:
    r = requests.get(BASE + path, params=params or {}, headers=_headers(login()), timeout=30)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- consultas
def lista_plantas() -> list[dict]:
    """Todas las plantas de la cuenta: [{id, plantName, ...}]."""
    d = _post("/api/plant/homePlantPage", {"keyWord": "", "pageIndex": 1, "pageSize": 500})
    return d.get("data", []) or []


def energia_planta(plant_id: int) -> dict:
    """Energía agregada en vivo: {loadPower(W), pvPower(kW), pvDayEnergy(Wh), pvTotalEnergy(kWh)}."""
    d = _get(f"/api/plant/AggPlantEnergy/{plant_id}")
    return d.get("data", d) or {}


def dispositivos_planta(plant_id: int) -> list[dict]:
    """Árbol de dispositivos de la planta (inversores, dataloggers, baterías).

    Cada nodo: {id, alias, dataLoggerSn, deviceSn, dtc, children}. El `dtc` es el
    tipo de equipo (se usa para traducir alarmas con el diccionario de modelos).
    """
    d = _get(f"/api/plant/getDeviceList/{plant_id}")
    data = d.get("data") or {}
    if isinstance(data, dict):
        return data.get("tree", []) or []
    return data or []


def home_planta(device_sn: str, dtc=None) -> dict:
    """Pantalla de inicio del equipo: modo, sistema, potencias y totales en vivo.

    El endpoint depende del tipo de equipo (dtc); getMgrnInverterV2 es el más común
    (cubre los dtc no listados). Incluye `workModelStr` (modo, ej. «Prioridad de
    batería»), `systemStateStr` (sistema, ej. «On Grid»), `pvPower`/`loadPower`/
    `gridPower`/`batPower` (kW) y totales de por vida en Wh (÷1000 → kWh).
    """
    dtc_s = str(dtc or "")
    if dtc_s in ("4098", "4099", "4102", "4103"):
        ruta = f"/api/home/getThInverterHomeV2/{device_sn}"
    elif dtc_s == "4100":
        ruta = f"/api/home/getMicInvHome/{device_sn}"
    elif dtc_s in ("61181", "105", "106", "107"):
        ruta = f"/api/home/getTQInverterHomeV2/{device_sn}"
    elif dtc_s in ("109", "110", "111", "65532"):
        ruta = f"/api/home/getSenergyHome/{device_sn}"
    elif dtc_s in ("112", "113", "114"):
        ruta = f"/api/home/getStkHome/{device_sn}"
    else:
        ruta = f"/api/home/getMgrnInverterV2/{device_sn}"
    d = _get(ruta)
    return d.get("data", d) or {}


def alarmas_planta(plant_id: int, device_sn: str = "", page_index: int = 0,
                   page_size: int = 100, device_type: str = "") -> list[dict]:
    """Alarmas/avisos de una planta (getPlantErrorList)."""
    d = _get("/api/home/getPlantErrorList", {
        "plantId": plant_id, "deviceSn": device_sn, "pageIndex": page_index,
        "pageSize": page_size, "deviceType": device_type,
    })
    return d.get("data", []) or []


def logs_firmware(plant_id: int, serial: str, page_index: int = 0,
                  page_size: int = 100) -> list[dict]:
    """Historial de actualizaciones de firmware de un dispositivo."""
    d = _post("/api/upgrade/getUpgradeLogs", {
        "planId": plant_id, "serialNumber": serial,
        "pageIndex": page_index, "pageSize": page_size,
    })
    return d.get("data", []) or []


def energia_inversor(serial_list, query_type: int, date: str) -> dict:
    """Serie de energía de un inversor (query_type: 1=día, 2=mes, 3=año...)."""
    d = _post("/api/home/getInverterHomeEnergyV2", {
        "serialNumberList": serial_list, "queryType": query_type, "date": date,
    })
    return d.get("data", d) or {}


def chart_campos(device_sn: str, tipo: int = 2) -> list[dict]:
    """Campos disponibles para la gráfica temporal (data/chart/queryField).

    Devuelve [{id, grupo, campo, etiqueta, unidad}] con las etiquetas ya en español.
    tipo 2/3/4 = energía (kWh); tipo 0/1 = potencia (puede venir vacío según equipo).
    """
    d = _get(f"/data/chart/queryField?deviceSn={device_sn}&type={tipo}")
    campos = []
    for g in (d.get("data") or []):
        for t in g.get("templates", []):
            campos.append({"id": t["id"], "grupo": g.get("groupName"),
                           "campo": t.get("fieldName"), "etiqueta": t.get("fieldLabel"),
                           "unidad": t.get("unit")})
    return campos


def chart_datos(device_sn: str, tipo: int, ids: list, inicio: str, fin: str) -> list[dict]:
    """Serie temporal de la gráfica (data/chart/getChartData), puntos cada 5 min.

    Cada punto: {<campo>: valor, ..., "time": "YYYY-MM-DD HH:MM"}.
    """
    d = _post("/data/chart/getChartData", {
        "type": tipo, "start": inicio, "end": fin,
        "queryItems": [{"ids": ids, "deviceSn": device_sn}],
    })
    return d.get("data") or []


# ---------------------------------------------------------------- alarmas ES
_TRADUCCION: dict | None = None


def _cargar_traduccion() -> dict:
    global _TRADUCCION
    if _TRADUCCION is None:
        ruta = Path(__file__).parent / "data" / "noor_traduccion_alarmas.json"
        try:
            _TRADUCCION = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _TRADUCCION = {}
    return _TRADUCCION


def traducir_alarma(label_en: str) -> str:
    """Traduce la etiqueta de una alarma al español (vacía si no la conoce)."""
    return _cargar_traduccion().get(label_en, label_en)
