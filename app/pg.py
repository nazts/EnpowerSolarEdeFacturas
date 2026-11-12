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

import atexit
import datetime as _dt
import threading
from typing import Any, Iterable, Sequence

_pool = None
_lock = threading.Lock()


def _normalizar(valor: Any) -> Any:
    """datetime/date -> texto, como devolvia SQLite (`2026-11-12 15:04:05`)."""
    if isinstance(valor, _dt.datetime):
        return valor.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(valor, _dt.date):
        return valor.isoformat()
    if isinstance(valor, _dt.timedelta):
        return str(valor)
    return valor


def _fila(valor):
    if valor is None:
        return None
    if isinstance(valor, dict):
        return {k: _normalizar(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return tuple(_normalizar(v) for v in valor)
    return _normalizar(valor)


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
        return _fila(self._c.fetchone())

    def fetchall(self):
        return [_fila(f) for f in self._c.fetchall()]

    def __iter__(self):
        for fila in self._c:
            yield _fila(fila)

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


def _obtener_pool(url: str):
    """Un pool por proceso (abrir conexiones TLS es caro)."""
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                from psycopg.rows import dict_row
                try:
                    from psycopg_pool import ConnectionPool
                except ModuleNotFoundError as exc:      # entorno a medio instalar
                    raise RuntimeError(
                        "Falta psycopg_pool (pool de conexiones a Supabase). "
                        "Instala las dependencias: pip install -r requirements.txt"
                    ) from exc

                _pool = ConnectionPool(
                    url, min_size=1, max_size=4, timeout=20, open=True,
                    kwargs={"row_factory": dict_row, "prepare_threshold": None},
                )
    return _pool


class ConexionPG:
    """Conexion a Postgres que se comporta como sqlite3.Connection."""

    driver = "postgres"

    def __init__(self, url: str, timeout: int = 15):
        self._pool = _obtener_pool(url)
        self.raw = self._pool.getconn(timeout=timeout)
        self._devuelta = False

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
        """No cierra: devuelve la conexion al pool (con rollback si quedo sucia)."""
        if self._devuelta:
            return
        self._devuelta = True
        try:
            if not self.raw.closed:
                self.raw.rollback()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._pool.putconn(self.raw)
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


def cerrar_pool() -> None:
    """Cierra el pool al salir (evita el aviso de hilos al apagar el proceso)."""
    global _pool
    if _pool is not None:
        try:
            _pool.close(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        finally:
            _pool = None


atexit.register(cerrar_pool)
