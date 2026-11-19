"""Reloj interno del panel: garantiza la corrida diaria de las 07:00.

El `systemd` timer (o la tarea de Windows) puede no dispararse —la PC estaba
apagada, la distro de WSL dormida, el servicio caído—. Este hilo corre dentro
del proceso del panel y, si a las 07:00 no hubo corrida, la lanza. Así la
búsqueda de facturas es automática aunque el planificador falle.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import threading
import time

from . import db

HORA = 7                 # 07:00, hora local del servidor
REINTENTOS = 4           # cuántas veces al día se intenta si algo falla
ESPERA = 45 * 60         # segundos entre intentos
INTERVALO = 60           # cada cuánto revisa el reloj
MENSAJE = "Corrida diaria"

_arrancado = False
_candado = threading.Lock()


# ------------------------------------------------------------------ consultas
def _texto(conn, sql: str, params=()):
    return conn.execute(sql, params).fetchall()


def intentos_de_hoy(fecha: str | None = None) -> int:
    """Cuántas veces se lanzó la corrida en el día (0 si todavía no)."""
    hoy = fecha or dt.date.today().isoformat()
    with db.sesion() as conn:
        filas = conn.execute(
            "SELECT COUNT(*) AS n FROM eventos"
            " WHERE mensaje = ? AND CAST(fecha AS TEXT) LIKE ?",
            (MENSAJE, f"{hoy}%")).fetchall()
    try:
        return int(filas[0]["n"])
    except (IndexError, KeyError, TypeError, ValueError):
        return 0


def ultimo_intento(fecha: str | None = None) -> dt.datetime | None:
    hoy = fecha or dt.date.today().isoformat()
    with db.sesion() as conn:
        fila = conn.execute(
            "SELECT MAX(CAST(fecha AS TEXT)) AS u FROM eventos"
            " WHERE mensaje = ? AND CAST(fecha AS TEXT) LIKE ?",
            (MENSAJE, f"{hoy}%")).fetchone()
    valor = (fila or {}).get("u") if hasattr(fila, "get") else (fila["u"] if fila else None)
    if not valor:
        return None
    try:
        return dt.datetime.fromisoformat(str(valor)[:19])
    except ValueError:
        return None


def debe_correr(ahora: dt.datetime | None = None) -> bool:
    """True si ya pasó la hora y la corrida de hoy falta, falló o quedó vieja."""
    ahora = ahora or dt.datetime.now()
    if ahora.hour < HORA:
        return False
    intentos = intentos_de_hoy(ahora.date().isoformat())
    if intentos >= REINTENTOS:
        return False
    if intentos == 0:
        return True
    ultimo = ultimo_intento(ahora.date().isoformat())
    if ultimo is None:
        return True
    pasado = (ahora - ultimo).total_seconds()
    if pasado < 0:
        return True      # las filas viejas se guardaban en UTC: no bloquea el día
    return pasado >= ESPERA


# ------------------------------------------------------------------- disparo
def ejecutar(forzar: bool = False) -> dict:
    """Lanza la corrida completa (con envío). Devuelve un resumen."""
    from . import runner

    if not forzar and not debe_correr():
        return {"corrida": False, "motivo": "ya se hizo o todavía no son las 07:00"}
    with _candado:
        try:
            res = runner.corrida(enviar=True)
            return {"corrida": True, "ok": True,
                    "enviadas": len(getattr(res, "enviadas", []) or []),
                    "pendientes": len(getattr(res, "pendientes", []) or [])}
        except Exception as exc:  # noqa: BLE001 - el hilo no debe morir
            try:
                with db.sesion() as conn:
                    db.log_evento(conn, "error", "La corrida automática falló",
                                  str(exc)[:300])
            finally:
                return {"corrida": True, "ok": False, "error": str(exc)[:300]}


def _bucle() -> None:
    while True:
        try:
            if debe_correr():
                ejecutar()
        except Exception:  # noqa: BLE001 - nunca tumbar el panel
            pass
        time.sleep(INTERVALO)


def arrancar() -> bool:
    """Enciende el hilo una sola vez por proceso."""
    global _arrancado
    if _arrancado:
        return False
    if os.environ.get("ENPOWER_AGENDA", "1") in ("0", "no", "off"):
        return False
    if "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.argv[0]:
        return False        # las pruebas nunca deben lanzar corridas reales
    _arrancado = True
    hilo = threading.Thread(target=_bucle, name="enpower-agenda", daemon=True)
    hilo.start()
    return True
