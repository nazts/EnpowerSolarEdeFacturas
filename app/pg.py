"""Capa de compatibilidad para usar Postgres (Supabase) con el codigo escrito para SQLite.

Todo el proyecto llama a `conn.execute("... ? ...", params)` y lee filas como si
fueran diccionarios (`fila["columna"]`, `dict(fila)`). En vez de reescribir las 40
consultas, aqui se envuelve la conexion de Postgres y se traduce:

  * `?`  ->  `%s`
  * filas -> dict (psycopg `dict_row`), con acceso por nombre
  * `cursor.lastrowid` -> `SELECT lastval()`
  * `PRAGMA ...` -> se ignora
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence


def _a_postgres(sql: str) -> str:
    """Cambia los marcadores `?` de SQLite por `%s` de Postgres.

    Respeta los `%` que ya existan (LIKE) escapandolos, y no toca los `?` que
    esten dentro de comillas simples.
    """
    salida: list[str] = []
    entre_comillas = False
    for ch in sql:
        if ch == "'":
            entre_comillas = not entre_comillas
        if ch == "%":
            salida.append("%%")
            continue
        if ch == "?" and not entre_comillas:
            salida.append("%s")
            continue
        salida.append(ch)
    return "".join(salida)


class CursorPG:
    """Envoltorio minimo de un cursor de psycopg con la cara de sqlite3."""

    def __init__(self, cursor):
        self._c = cursor

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def __iter__(self):
        return iter(self._c)

    @property
    def rowcount(self) -> int:
        return self._c.rowcount

    @property
    def lastrowid(self) -> int:
        """Postgres no tiene lastrowid: se pregunta por el ultimo valor de secuencia."""
        try:
            self._c.execute("SELECT lastval() AS id")
            fila = self._c.fetchone()
            return int(fila["id"]) if fila else 0
        except Exception:  # noqa: BLE001 - sin secuencia usada en la sesion
            return 0


class ConexionPG:
    """Conexion a Postgres que se comporta como sqlite3.Connection."""

    def __init__(self, url: str, timeout: int = 15):
        import psycopg
        from psycopg.rows import dict_row

        # prepare_threshold=None: el pooler de Supabase trabaja en modo
        # transaccion y no admite sentencias preparadas con nombre.
        self.raw = psycopg.connect(url, row_factory=dict_row, connect_timeout=timeout,
                                   autocommit=False, prepare_threshold=None)
        self.driver = "postgres"

    # ------------------------------------------------------------- ejecucion
    def execute(self, sql: str, params: Sequence[Any] | None = None) -> CursorPG:
        sentencia = _a_postgres(sql)
        cursor = self.raw.cursor()
        if params is None:
            cursor.execute(sentencia)
        else:
            cursor.execute(sentencia, list(params))
        return CursorPG(cursor)

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> CursorPG:
        cursor = self.raw.cursor()
        cursor.executemany(_a_postgres(sql), list(seq))
        return CursorPG(cursor)

    def executescript(self, sql: str) -> None:
        """Igual que en sqlite3, pero sentencia por sentencia."""
        cursor = self.raw.cursor()
        cursor.execute(sql)

    # ------------------------------------------------------------- transaccion
    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        try:
            self.raw.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def es_postgres(url: str | None) -> bool:
    return bool(url) and str(url).startswith(("postgres://", "postgresql://"))


def limpiar_url(url: str) -> str:
    """Quita espacios y normaliza el esquema `postgres://` a `postgresql://`."""
    url = (url or "").strip().strip('"').strip("'")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url
