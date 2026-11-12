"""Calculos de fecha de corte."""
from __future__ import annotations

import calendar
from datetime import date


def dia_efectivo(dia: int | None, anio: int, mes: int) -> int | None:
    """Dia real del mes (28/29/30/31 segun corresponda). Nunca lanza."""
    try:
        valor = int(dia) if dia else 0
    except (TypeError, ValueError):
        return None
    if valor < 1:
        return None
    return min(valor, calendar.monthrange(anio, mes)[1])


def proximo_corte(dia_corte: int | None, desde: date | None = None) -> date | None:
    """Proxima fecha de corte a partir de `desde` (hoy por defecto)."""
    desde = desde or date.today()
    if not dia_corte:
        return None
    for delta in (0, 1):
        anio, mes = desde.year, desde.month + delta
        if mes > 12:
            anio, mes = anio + 1, 1
        candidato = date(anio, mes, dia_efectivo(dia_corte, anio, mes) or 1)
        if candidato >= desde:
            return candidato
    return None


def dias_para_corte(dia_corte: int | None, desde: date | None = None) -> int | None:
    prox = proximo_corte(dia_corte, desde)
    return None if prox is None else (prox - (desde or date.today())).days
