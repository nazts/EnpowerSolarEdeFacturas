"""Base de datos SQLite del proyecto: contratos, facturas, pendientes, envios."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from . import config
from .naming import normalizar_razon_social

SCHEMA = """
CREATE TABLE IF NOT EXISTS contratos (
    nic                TEXT PRIMARY KEY,
    razon_social       TEXT NOT NULL DEFAULT '',
    nombre_normalizado TEXT NOT NULL DEFAULT '',
    alias              TEXT NOT NULL DEFAULT '',
    asesor             TEXT NOT NULL DEFAULT '',
    dia_corte          INTEGER,
    activo             INTEGER NOT NULL DEFAULT 1,
    notas              TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS facturas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nic           TEXT NOT NULL,
    ref_pago      TEXT NOT NULL UNIQUE,
    no_factura    TEXT,
    tipo          TEXT,
    cliente       TEXT,
    periodo_ini   TEXT,
    periodo_fin   TEXT,
    corte         TEXT,
    emision       TEXT,
    vence         TEXT,
    dias          INTEGER,
    kwh           REAL,
    sin_subsidio  REAL,
    subsidio      REAL,
    facturado     REAL,
    balance       REAL,
    pdf_path      TEXT,
    estado        TEXT NOT NULL DEFAULT 'descargada',
    enviada       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_facturas_nic ON facturas(nic);
CREATE INDEX IF NOT EXISTS idx_facturas_corte ON facturas(corte);

CREATE TABLE IF NOT EXISTS pendientes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nic            TEXT NOT NULL,
    fecha_corte    TEXT NOT NULL,
    primer_intento TEXT NOT NULL,
    ultimo_intento TEXT NOT NULL,
    intentos       INTEGER NOT NULL DEFAULT 1,
    estado         TEXT NOT NULL DEFAULT 'pendiente',
    detalle        TEXT NOT NULL DEFAULT '',
    UNIQUE(nic, fecha_corte)
);

CREATE TABLE IF NOT EXISTS envios (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha         TEXT NOT NULL,
    chat_id       TEXT NOT NULL,
    message_id    TEXT,
    facturas      INTEGER NOT NULL DEFAULT 0,
    detalle       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usuarios (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario       TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    nombre        TEXT NOT NULL DEFAULT '',
    rol           TEXT NOT NULL DEFAULT 'asesor',
    activo        INTEGER NOT NULL DEFAULT 1,
    creado_en     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ultimo_acceso TEXT
);

CREATE TABLE IF NOT EXISTS proyectos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nic                 TEXT NOT NULL,
    nombre              TEXT NOT NULL DEFAULT '',
    paneles             TEXT NOT NULL DEFAULT '',
    capacidad_instalada REAL,
    inversores          TEXT NOT NULL DEFAULT '',
    bateria             TEXT NOT NULL DEFAULT '',
    capacidad_bateria   REAL,
    fecha_instalacion   TEXT NOT NULL DEFAULT '',
    asesor              TEXT NOT NULL DEFAULT '',
    notas               TEXT NOT NULL DEFAULT '',
    creado_en           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_proyectos_nic ON proyectos(nic);

CREATE TABLE IF NOT EXISTS eventos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    nivel      TEXT NOT NULL,
    mensaje    TEXT NOT NULL,
    detalle    TEXT NOT NULL DEFAULT ''
);
"""


# columnas agregadas despues de la v1 (se crean con ALTER TABLE si faltan)
COLUMNAS_NUEVAS = {
    "proveedor": "TEXT NOT NULL DEFAULT 'edenorte'",
    "direccion": "TEXT NOT NULL DEFAULT ''",
    "telefono": "TEXT NOT NULL DEFAULT ''",
    "latitud": "REAL",
    "longitud": "REAL",
    "paneles": "TEXT NOT NULL DEFAULT ''",
    "potencia_paneles": "REAL",
    "inversores": "TEXT NOT NULL DEFAULT ''",
    "bateria": "TEXT NOT NULL DEFAULT ''",
    "capacidad_bateria": "REAL",
    "fecha_instalacion": "TEXT NOT NULL DEFAULT ''",
}


def migrar(conn: sqlite3.Connection) -> None:
    """Agrega columnas que falten en bases ya creadas."""
    existentes = {r[1] for r in conn.execute("PRAGMA table_info(contratos)")}
    for nombre, tipo in COLUMNAS_NUEVAS.items():
        if nombre not in existentes:
            conn.execute(f"ALTER TABLE contratos ADD COLUMN {nombre} {tipo}")
    conn.commit()


def hoy() -> str:
    return date.today().isoformat()


def ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def conectar(db_path: Path | str | None = None) -> sqlite3.Connection:
    ruta = Path(db_path or config.DB_PATH)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    cerrar = conn is None
    conn = conn or conectar()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        migrar(conn)
    finally:
        if cerrar:
            conn.close()


@contextmanager
def sesion(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    conn = conectar(db_path)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------ contratos
def upsert_contrato(conn: sqlite3.Connection, nic: str, razon_social: str = "",
                    alias: str = "", asesor: str = "", dia_corte: int | None = None,
                    activo: int | None = None, notas: str = "", direccion: str = "",
                    telefono: str = "", latitud: float | None = None,
                    longitud: float | None = None, paneles: str = "",
                    potencia_paneles: float | None = None,
                    inversores: str = "", bateria: str = "",
                    capacidad_bateria: float | None = None,
                    fecha_instalacion: str = "", proveedor: str = "") -> None:
    """Crea o actualiza un contrato. Los campos vacios no pisan lo ya guardado."""
    nic = str(nic).strip()
    norm = normalizar_razon_social(razon_social)
    fila = conn.execute("SELECT nic FROM contratos WHERE nic = ?", (nic,)).fetchone()
    if fila:
        campos, valores = [], []

        def poner(col: str, val) -> None:
            campos.append(f"{col} = ?")
            valores.append(val)

        if razon_social:
            poner("razon_social", razon_social.strip())
            poner("nombre_normalizado", norm)
        if proveedor:
            poner("proveedor", proveedor.strip().lower())
        for col, val in (("alias", alias), ("asesor", asesor), ("notas", notas),
                         ("direccion", direccion), ("telefono", telefono),
                         ("paneles", paneles), ("inversores", inversores), ("bateria", bateria),
                         ("fecha_instalacion", fecha_instalacion)):
            if val:
                poner(col, val.strip())
        for col, val in (("dia_corte", dia_corte), ("latitud", latitud),
                         ("longitud", longitud), ("potencia_paneles", potencia_paneles),
                         ("capacidad_bateria", capacidad_bateria)):
            if val is not None:
                poner(col, val)
        if activo is not None:                    # solo si se pide explicitamente
            poner("activo", int(activo))
        poner("updated_at", ahora())
        valores.append(nic)
        conn.execute(f"UPDATE contratos SET {', '.join(campos)} WHERE nic = ?", valores)
    else:
        conn.execute(
            """INSERT INTO contratos
               (nic, razon_social, nombre_normalizado, alias, asesor, dia_corte, activo, notas,
                direccion, telefono, latitud, longitud, paneles, potencia_paneles, inversores,
                bateria, capacidad_bateria, fecha_instalacion, proveedor)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (nic, razon_social.strip(), norm, alias.strip(), asesor.strip(),
             int(dia_corte) if dia_corte is not None else None,
             1 if activo is None else int(activo), notas,
             direccion.strip(), telefono.strip(), latitud, longitud, paneles.strip(),
             potencia_paneles, inversores.strip(), bateria.strip(), capacidad_bateria,
             fecha_instalacion.strip(), (proveedor or "edenorte").strip().lower()),
        )
    conn.commit()


CAMPOS_EQUIPO = ("direccion", "telefono", "latitud", "longitud", "paneles",
                "potencia_paneles", "inversores", "bateria", "capacidad_bateria",
                "fecha_instalacion")


def actualizar_equipo(conn: sqlite3.Connection, nic: str, datos: dict) -> None:
    """Reemplaza ubicacion/equipo tal cual se envia (permite vaciar campos)."""
    campos = {k: v for k, v in datos.items() if k in CAMPOS_EQUIPO}
    if not campos:
        return
    sets = ", ".join(f"{k} = ?" for k in campos)
    conn.execute(f"UPDATE contratos SET {sets}, updated_at = ? WHERE nic = ?",
                 [*campos.values(), ahora(), str(nic)])
    conn.commit()


CAMPOS_EDITABLES = ("razon_social", "alias", "asesor", "dia_corte", "activo", "notas",
                    "proveedor", "direccion", "telefono", "latitud", "longitud",
                    "paneles", "potencia_paneles", "inversores", "bateria",
                    "capacidad_bateria", "fecha_instalacion")


def actualizar_contrato(conn: sqlite3.Connection, nic: str, datos: dict) -> bool:
    """Edita la ficha completa del cliente tal cual se envia (permite vaciar campos).

    A diferencia de `upsert_contrato`, aqui un campo vacio SI se guarda vacio:
    es el formulario de edicion del administrador.
    """
    campos = {k: v for k, v in datos.items() if k in CAMPOS_EDITABLES}
    if not campos:
        return False
    if "razon_social" in campos:
        campos["nombre_normalizado"] = normalizar_razon_social(campos["razon_social"] or "")
    sets = ", ".join(f"{k} = ?" for k in campos)
    cur = conn.execute(f"UPDATE contratos SET {sets}, updated_at = ? WHERE nic = ?",
                       [*campos.values(), ahora(), str(nic)])
    conn.commit()
    return cur.rowcount > 0


def listar_contratos(conn: sqlite3.Connection, solo_activos: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM contratos"
    if solo_activos:
        sql += " WHERE activo = 1"
    sql += " ORDER BY nic"
    return conn.execute(sql).fetchall()


def obtener_contrato(conn: sqlite3.Connection, nic: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM contratos WHERE nic = ?", (str(nic),)).fetchone()


def eliminar_contrato(conn: sqlite3.Connection, nic: str) -> None:
    conn.execute("DELETE FROM contratos WHERE nic = ?", (str(nic),))
    # sin contrato no tiene sentido seguir reintentando sus pendientes
    conn.execute("DELETE FROM pendientes WHERE nic = ?", (str(nic),))
    conn.commit()


# ------------------------------------------------------------------- facturas
def guardar_factura(conn: sqlite3.Connection, datos: dict) -> int:
    """Inserta o actualiza la factura SIN perder su id ni el estado de envio.

    `INSERT OR REPLACE` borra y recrea la fila: cambiaba el id y dejaba
    `enviada=0`, asi que la misma factura se reenviaba a Telegram. Aqui se hace
    upsert por `ref_pago` (que es UNIQUE) conservando id/enviada/created_at.
    """
    datos = {k: v for k, v in datos.items() if k != "id"}
    campos = list(datos)
    marcas = ",".join("?" for _ in campos)
    ref = datos.get("ref_pago")
    if not ref:                       # sin referencia no hay upsert posible
        cur = conn.execute(
            f"INSERT INTO facturas ({','.join(campos)}) VALUES ({marcas})",
            list(datos.values()))
        conn.commit()
        return int(cur.lastrowid or 0)

    actualiza = ", ".join(f"{c}=excluded.{c}" for c in campos if c != "ref_pago")
    conn.execute(
        f"INSERT INTO facturas ({','.join(campos)}) VALUES ({marcas}) "
        f"ON CONFLICT(ref_pago) DO UPDATE SET {actualiza}",
        list(datos.values()),
    )
    conn.commit()
    fila = conn.execute("SELECT id FROM facturas WHERE ref_pago = ?", (ref,)).fetchone()
    return int(fila["id"]) if fila else 0


def factura_existe(conn: sqlite3.Connection, ref_pago: str) -> bool:
    return conn.execute("SELECT 1 FROM facturas WHERE ref_pago = ?",
                        (str(ref_pago),)).fetchone() is not None


def factura_ya_enviada(conn: sqlite3.Connection, ref_pago: str) -> bool:
    """True si esa factura ya se publico en el canal (no se vuelve a mandar)."""
    fila = conn.execute("SELECT enviada FROM facturas WHERE ref_pago = ?",
                        (str(ref_pago),)).fetchone()
    return bool(fila and fila["enviada"])


def facturas_del_dia(conn: sqlite3.Connection, fecha: str | None = None) -> list[sqlite3.Row]:
    fecha = fecha or hoy()
    return conn.execute(
        "SELECT * FROM facturas WHERE corte = ? AND estado = 'descargada' ORDER BY nic", (fecha,)
    ).fetchall()


def facturas_sin_enviar(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM facturas WHERE enviada = 0 AND estado = 'descargada' ORDER BY nic"
    ).fetchall()


def marcar_enviadas(conn: sqlite3.Connection, ids: list[int], chat_id: str) -> None:
    if not ids:
        return
    marcas = ",".join("?" for _ in ids)
    conn.execute(f"UPDATE facturas SET enviada = 1 WHERE id IN ({marcas})", ids)
    conn.commit()


def ultima_factura(conn: sqlite3.Connection, nic: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM facturas WHERE nic = ? ORDER BY emision DESC LIMIT 1", (str(nic),)
    ).fetchone()


def historico_facturas(conn: sqlite3.Connection, nic: str) -> list[sqlite3.Row]:
    """Todas las facturas descargadas de un contrato, la mas reciente primero."""
    return conn.execute(
        """SELECT * FROM facturas
           WHERE nic = ? AND estado = 'descargada'
           ORDER BY corte DESC, id DESC""",
        (str(nic),),
    ).fetchall()


def clientes_mapa(conn: sqlite3.Connection) -> list[dict]:
    """Clientes con ubicacion y equipo instalado, para el modulo de mapa."""
    filas = conn.execute(
        """SELECT c.*,
                  (SELECT COUNT(*) FROM facturas f WHERE f.nic = c.nic AND f.estado='descargada') AS total_facturas,
                  (SELECT f.facturado FROM facturas f WHERE f.nic = c.nic AND f.estado='descargada'
                    ORDER BY f.corte DESC LIMIT 1) AS ultimo_monto,
                  (SELECT f.kwh FROM facturas f WHERE f.nic = c.nic AND f.estado='descargada'
                    ORDER BY f.corte DESC LIMIT 1) AS ultimo_kwh,
                  (SELECT MAX(f.corte) FROM facturas f WHERE f.nic = c.nic AND f.estado='descargada') AS ultimo_corte
           FROM contratos c ORDER BY c.nic"""
    ).fetchall()
    return [dict(f) for f in filas]


# ------------------------------------------------------------------ proyectos
def crear_proyecto(conn: sqlite3.Connection, datos: dict) -> int:
    campos = ["nic", "nombre", "paneles", "capacidad_instalada", "inversores",
              "bateria", "capacidad_bateria", "fecha_instalacion", "asesor", "notas"]
    valores = [datos.get(c) if datos.get(c) not in ("", None) else
               (None if c in ("capacidad_instalada", "capacidad_bateria") else "")
               for c in campos]
    cursor = conn.execute(
        f"INSERT INTO proyectos ({','.join(campos)}) VALUES ({','.join('?' for _ in campos)})",
        valores,
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def listar_proyectos(conn: sqlite3.Connection, nic: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM proyectos WHERE nic = ? ORDER BY fecha_instalacion DESC, id DESC",
        (str(nic),),
    ).fetchall()


def eliminar_proyecto(conn: sqlite3.Connection, proyecto_id: int) -> None:
    conn.execute("DELETE FROM proyectos WHERE id = ?", (int(proyecto_id),))
    conn.commit()


def resumen_dashboard(conn: sqlite3.Connection) -> dict:
    """Datos agregados para la vista grafica del panel."""
    clientes = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN activo = 1 THEN 1 ELSE 0 END) AS activos,
                  SUM(CASE WHEN dia_corte IS NULL THEN 1 ELSE 0 END) AS sin_corte
           FROM contratos"""
    ).fetchone()
    facturas = conn.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(facturado), 0) AS monto,
                  COALESCE(SUM(kwh), 0) AS kwh,
                  SUM(CASE WHEN enviada = 0 THEN 1 ELSE 0 END) AS sin_enviar
           FROM facturas WHERE estado = 'descargada'"""
    ).fetchone()
    por_dia: dict[int, int] = {}
    # cortes por dia del mes, calculado en Python para no depender de fechas
    for fila in conn.execute("SELECT dia_corte FROM contratos WHERE dia_corte IS NOT NULL"):
        dia = int(fila["dia_corte"])
        por_dia[dia] = por_dia.get(dia, 0) + 1
    meses = [dict(r) for r in conn.execute(
        """SELECT substr(corte, 1, 7) AS mes, COUNT(*) AS facturas,
                  COALESCE(SUM(facturado), 0) AS monto, COALESCE(SUM(kwh), 0) AS kwh
           FROM facturas WHERE estado = 'descargada' AND corte IS NOT NULL
           GROUP BY mes ORDER BY mes DESC LIMIT 12"""
    )]
    return {
        "clientes": dict(clientes),
        "facturas": dict(facturas),
        "pendientes": len(pendientes_abiertos(conn)),
        "cortes_por_dia": [{"dia": d, "clientes": por_dia.get(d, 0)} for d in range(1, 32)],
        "facturas_por_mes": list(reversed(meses)),
    }


def resumen_clientes(conn: sqlite3.Connection) -> list[dict]:
    """Contratos con conteo de facturas y datos de la ultima factura."""
    filas = conn.execute(
        """SELECT c.*,
                  (SELECT COUNT(*) FROM facturas f
                    WHERE f.nic = c.nic AND f.estado = 'descargada') AS total_facturas,
                  (SELECT f.corte FROM facturas f
                    WHERE f.nic = c.nic AND f.estado = 'descargada'
                    ORDER BY f.corte DESC LIMIT 1) AS ultimo_corte,
                  (SELECT f.facturado FROM facturas f
                    WHERE f.nic = c.nic AND f.estado = 'descargada'
                    ORDER BY f.corte DESC LIMIT 1) AS ultimo_monto,
                  (SELECT f.kwh FROM facturas f
                    WHERE f.nic = c.nic AND f.estado = 'descargada'
                    ORDER BY f.corte DESC LIMIT 1) AS ultimo_kwh
           FROM contratos c
           ORDER BY c.activo DESC, c.nic"""
    ).fetchall()
    return [dict(f) for f in filas]


# ----------------------------------------------------------------- pendientes
def registrar_pendiente(conn: sqlite3.Connection, nic: str, fecha_corte: str,
                        detalle: str = "") -> None:
    fila = conn.execute(
        "SELECT id, intentos FROM pendientes WHERE nic = ? AND fecha_corte = ?",
        (str(nic), fecha_corte),
    ).fetchone()
    if fila:
        intentos = fila["intentos"] + 1
        estado = "atrasado" if intentos >= 3 else "pendiente"
        conn.execute(
            "UPDATE pendientes SET intentos = ?, ultimo_intento = ?, estado = ?, detalle = ? WHERE id = ?",
            (intentos, ahora(), estado, detalle, fila["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO pendientes (nic, fecha_corte, primer_intento, ultimo_intento, intentos, estado, detalle)
               VALUES (?,?,?,?,1,'pendiente',?)""",
            (str(nic), fecha_corte, ahora(), ahora(), detalle),
        )
    conn.commit()


def cerrar_pendiente(conn: sqlite3.Connection, nic: str, fecha_corte: str) -> None:
    conn.execute(
        "UPDATE pendientes SET estado = 'resuelto', ultimo_intento = ? WHERE nic = ? AND fecha_corte = ?",
        (ahora(), str(nic), fecha_corte),
    )
    conn.commit()


def pendientes_abiertos(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pendientes WHERE estado IN ('pendiente','atrasado') ORDER BY fecha_corte"
    ).fetchall()


# --------------------------------------------------------------------- eventos
def log_evento(conn: sqlite3.Connection, nivel: str, mensaje: str, detalle: str = "") -> None:
    conn.execute("INSERT INTO eventos (nivel, mensaje, detalle) VALUES (?,?,?)",
                 (nivel, mensaje, detalle))
    conn.commit()


def ultimos_eventos(conn: sqlite3.Connection, limite: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM eventos ORDER BY id DESC LIMIT ?", (limite,)
    ).fetchall()
