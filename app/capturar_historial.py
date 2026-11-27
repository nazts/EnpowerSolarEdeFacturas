"""Captura snapshots de potencia de todas las plantas Noor y los guarda en BD.

Se ejecuta periódicamente (cada 5-10 min) vía cron/tarea programada.
Uso: python -m app.capturar_historial
"""
from __future__ import annotations

from datetime import datetime

from . import db, noor


def capturar_todas() -> None:
    """Captura snapshots de todas las plantas Noor activas y los guarda."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # obtener plantas activas de la BD (deben existir previamente vía monitoreo)
    with db.sesion() as conn:
        plantas_registradas = conn.execute(
            "SELECT serial_number, alias FROM plantas_noor WHERE activo = 1"
        ).fetchall()
    
    if not plantas_registradas:
        print(f"{timestamp} | Sin plantas registradas (usar el panel de monitoreo primero)")
        return
    
    # capturar snapshot de cada planta
    capturados = 0
    errores = 0
    for p in plantas_registradas:
        serial = p["serial_number"]
        try:
            snap = noor.capturar_snapshot(serial)
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
            capturados += 1
        except Exception as exc:  # noqa: BLE001
            print(f"Error {serial}: {exc}")
            errores += 1
    
    print(f"{timestamp} | {capturados} snapshots, {errores} errores")


if __name__ == "__main__":
    capturar_todas()
