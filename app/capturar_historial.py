"""Captura snapshots de potencia de todas las plantas Noor y los guarda en BD.

Se ejecuta periódicamente (cada 5-10 min) vía cron/tarea programada.
Uso: python -m app.capturar_historial
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from . import db, noor

# SQLite local de respaldo (acumula el historial en la laptop sin depender de la red)
LOCAL_DB = Path(__file__).resolve().parent.parent / "data" / "historial_local.db"


def _guardar_local(serial: str, timestamp: str, snap: dict) -> bool:
    """Guarda el snapshot en el SQLite local. Devuelve True si se guardó."""
    try:
        LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(LOCAL_DB))
        con.execute("""CREATE TABLE IF NOT EXISTS historial_potencia (
            serial_number   TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            pv_w            REAL, carga_w REAL, eps_w REAL, external_w REAL,
            red_w           REAL, bateria_w REAL, soc REAL,
            bat_voltage     REAL, bat_current REAL,
            charge_current  REAL, discharge_current REAL,
            UNIQUE(serial_number, timestamp)
        )""")
        con.execute(
            """INSERT OR IGNORE INTO historial_potencia
               (serial_number, timestamp, pv_w, carga_w, eps_w, external_w,
                red_w, bateria_w, soc, bat_voltage, bat_current,
                charge_current, discharge_current)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (serial, timestamp,
             snap.get("pv_w"), snap.get("carga_w"), snap.get("eps_w"),
             snap.get("external_w"), snap.get("red_w"), snap.get("bateria_w"),
             snap.get("soc"), snap.get("bat_voltage"), snap.get("bat_current"),
             snap.get("charge_current"), snap.get("discharge_current")),
        )
        con.commit()
        con.close()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  (local) Error guardando {serial}: {exc}")
        return False


def _guardar_remoto(serial: str, timestamp: str, snap: dict) -> bool:
    """Guarda el snapshot en la BD Postgres. Devuelve True si se guardó."""
    try:
        with db.sesion() as conn:
            conn.execute(
                """INSERT INTO historial_potencia
                   (serial_number, timestamp, pv_w, carga_w, eps_w, external_w,
                    red_w, bateria_w, soc, bat_voltage, bat_current,
                    charge_current, discharge_current)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(serial_number, timestamp) DO NOTHING""",
                (serial, timestamp,
                 snap.get("pv_w"), snap.get("carga_w"), snap.get("eps_w"),
                 snap.get("external_w"), snap.get("red_w"), snap.get("bateria_w"),
                 snap.get("soc"), snap.get("bat_voltage"), snap.get("bat_current"),
                 snap.get("charge_current"), snap.get("discharge_current")),
            )
            conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  (remoto) Error guardando {serial}: {exc}")
        return False


def capturar_todas() -> None:
    """Captura snapshots de todas las plantas Noor activas y los guarda."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # obtener plantas activas (de la remota; si falla, del SQLite local)
    plantas_registradas = []
    try:
        with db.sesion() as conn:
            plantas_registradas = conn.execute(
                "SELECT serial_number, alias FROM plantas_noor WHERE activo = 1"
            ).fetchall()
    except Exception:  # noqa: BLE001 — la remota no responde, usamos el local
        try:
            con = sqlite3.connect(str(LOCAL_DB))
            plantas_registradas = con.execute(
                "SELECT DISTINCT serial_number AS serial_number, "
                "serial_number AS alias FROM historial_potencia"
            ).fetchall()
            con.close()
        except Exception:  # noqa: BLE001
            plantas_registradas = []

    if not plantas_registradas:
        print(f"{timestamp} | Sin plantas registradas (usar el panel de monitoreo primero)")
        return

    capturados = 0
    errores = 0
    for p in plantas_registradas:
        serial = p["serial_number"]
        try:
            snap = noor.capturar_snapshot(serial)
            _guardar_local(serial, timestamp, snap)
            _guardar_remoto(serial, timestamp, snap)
            capturados += 1
        except Exception as exc:  # noqa: BLE001
            print(f"Error {serial}: {exc}")
            errores += 1

    print(f"{timestamp} | {capturados} snapshots, {errores} errores")


if __name__ == "__main__":
    capturar_todas()
