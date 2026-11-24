"""Base de datos SQLite del proyecto: contratos, facturas, pendientes, envios."""
from __future__ import annotations

import sqlite3
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from . import config, pg

# quien esta haciendo la peticion (lo pone el middleware del panel)
usuario_en_curso: ContextVar[str] = ContextVar("usuario_en_curso", default="")
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
    estado             TEXT NOT NULL DEFAULT 'agregado',
    borrado_at         TEXT,
    titular_pago       TEXT NOT NULL DEFAULT '',
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
    tg_file_id    TEXT,
    tg_message_id TEXT,
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

CREATE TABLE IF NOT EXISTS cotizaciones (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    numero            TEXT NOT NULL,
    fecha             TEXT NOT NULL,
    cliente_nombre    TEXT NOT NULL DEFAULT '',
    cliente_rnc       TEXT NOT NULL DEFAULT '',
    cliente_doc_tipo  TEXT NOT NULL DEFAULT 'RNC',
    cliente_direccion TEXT NOT NULL DEFAULT '',
    moneda            TEXT NOT NULL DEFAULT 'Pesos Dominicanos',
    items             TEXT NOT NULL DEFAULT '[]',
    subtotal          REAL NOT NULL DEFAULT 0,
    itbis             REAL NOT NULL DEFAULT 0,
    comision_pct      REAL NOT NULL DEFAULT 4.5,
    comision          REAL NOT NULL DEFAULT 0,
    total             REAL NOT NULL DEFAULT 0,
    notas             TEXT NOT NULL DEFAULT '',
    firma             TEXT NOT NULL DEFAULT '',
    firma_id          INTEGER,
    token             TEXT,
    creado_por        TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS firmas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT NOT NULL DEFAULT '',
    cargo       TEXT NOT NULL DEFAULT '',
    archivo     TEXT NOT NULL DEFAULT '',
    ancho       REAL NOT NULL DEFAULT 160,
    x           REAL,
    y           REAL,
    activo      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usuarios (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario       TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    nombre        TEXT NOT NULL DEFAULT '',
    rol           TEXT NOT NULL DEFAULT 'asesor',
    activo        INTEGER NOT NULL DEFAULT 1,
    modulos       TEXT NOT NULL DEFAULT '',
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
    detalle    TEXT NOT NULL DEFAULT '',
    usuario    TEXT NOT NULL DEFAULT ''
);
"""


# columnas agregadas despues de la v1 (se crean con ALTER TABLE si faltan)
def _deshacer(conn) -> None:
    """Cierra la transacción en curso tras un error.

    En Postgres un SELECT fallido deja la transacción abortada y el ALTER
    siguiente falla en silencio: sin rollback, la columna nunca se creaba.
    """
    for metodo in ("rollback", "commit"):
        funcion = getattr(conn, metodo, None)
        if callable(funcion):
            try:
                funcion()
                return
            except Exception:  # noqa: BLE001
                continue


def _asegurar_columnas(conn, tabla: str, columnas: dict[str, str]) -> None:
    """Agrega columnas que falten sin romper en SQLite ni en Postgres.

    `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` no existe en SQLite y el
    try/except se tragaba el error, así que la columna nunca se creaba.
    """
    for columna, tipo in columnas.items():
        try:
            conn.execute(f"SELECT {columna} FROM {tabla} LIMIT 1")
            continue                      # ya existe
        except Exception:  # noqa: BLE001 - todavía no está
            _deshacer(conn)               # en Postgres la transacción queda abortada
        try:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")
            conn.commit()
        except Exception:  # noqa: BLE001 - otro proceso la creó
            _deshacer(conn)
    conn.commit()


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
    # 'agregado' mientras el cliente este en el panel, 'borrado' si se elimino
    "estado": "TEXT NOT NULL DEFAULT 'agregado'",
    # cuando se mando a la papelera (para purgarla a los 30 dias)
    "borrado_at": "TEXT",
    # quien paga el proyecto fotovoltaico (no es el titular de la factura)
    "titular_pago": "TEXT NOT NULL DEFAULT ''",
}


def migrar(conn: sqlite3.Connection) -> None:
    """Agrega columnas que falten en bases ya creadas."""
    if getattr(conn, "driver", "sqlite") == "postgres":
        # el esquema de Postgres ya trae los ALTER ... IF NOT EXISTS
        for columna, tipo in COLUMNAS_NUEVAS.items():
            try:
                conn.execute(f"ALTER TABLE contratos ADD COLUMN IF NOT EXISTS {columna} {tipo}")
            except Exception:  # noqa: BLE001
                pass
        _asegurar_columnas(conn, "usuarios", {"modulos": "TEXT NOT NULL DEFAULT ''"})
        _asegurar_columnas(conn, "cotizaciones",
                           {"firma": "TEXT NOT NULL DEFAULT ''", "token": "TEXT",
                            "cliente_doc_tipo": "TEXT NOT NULL DEFAULT 'RNC'",
                            "firma_id": "INTEGER"})
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS cotizaciones_token"
                         " ON cotizaciones(token)")
        except Exception:  # noqa: BLE001 - se crea en la próxima inicialización
            pass
        for columna in ("tg_file_id", "tg_message_id"):
            try:
                conn.execute(f"ALTER TABLE facturas ADD COLUMN IF NOT EXISTS {columna} TEXT")
            except Exception:  # noqa: BLE001
                pass
        conn.commit()
        return
    columnas_eventos = {r[1] for r in conn.execute("PRAGMA table_info(eventos)")}
    if "usuario" not in columnas_eventos:
        conn.execute("ALTER TABLE eventos ADD COLUMN usuario TEXT NOT NULL DEFAULT ''")
        conn.commit()
    existentes = {r[1] for r in conn.execute("PRAGMA table_info(contratos)")}
    for nombre, tipo in COLUMNAS_NUEVAS.items():
        if nombre not in existentes:
            conn.execute(f"ALTER TABLE contratos ADD COLUMN {nombre} {tipo}")
    columnas_usuarios = {r[1] for r in conn.execute("PRAGMA table_info(usuarios)")}
    if "modulos" not in columnas_usuarios:
        conn.execute("ALTER TABLE usuarios ADD COLUMN modulos TEXT NOT NULL DEFAULT ''")
    # cotizaciones: firma de quien firma y token anti-duplicado
    columnas_cot = {r[1] for r in conn.execute("PRAGMA table_info(cotizaciones)")}
    for nombre, tipo in (("firma", "TEXT NOT NULL DEFAULT ''"), ("token", "TEXT"),
                         ("cliente_doc_tipo", "TEXT NOT NULL DEFAULT 'RNC'"),
                         ("firma_id", "INTEGER")):
        if nombre not in columnas_cot:
            conn.execute(f"ALTER TABLE cotizaciones ADD COLUMN {nombre} {tipo}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS cotizaciones_token"
                 " ON cotizaciones(token)")
    conn.commit()


def hoy() -> str:
    return date.today().isoformat()


def ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def conectar(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Devuelve la conexion segun `DATABASE_URL` (Postgres) o SQLite (defecto)."""
    url = pg.limpiar_url(config.DATABASE_URL)
    if pg.es_postgres(url) and db_path is None:
        return pg.ConexionPG(url)
    ruta = Path(db_path or config.DB_PATH)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_INICIALIZADA = False


def init_db(conn: sqlite3.Connection | None = None, forzar: bool = False) -> None:
    """Crea las tablas si faltan. En Postgres se hace una sola vez por proceso."""
    global _INICIALIZADA
    es_pg = getattr(conn, "driver", "") == "postgres" if conn is not None else False
    if _INICIALIZADA and not forzar and (conn is None or es_pg):
        return
    cerrar = conn is None
    conn = conn or conectar()
    try:
        if getattr(conn, "driver", "sqlite") == "postgres":
            esquema = (Path(__file__).with_name("schema_postgres.sql")).read_text(encoding="utf-8")
            conn.executescript(esquema)
            conn.commit()
            migrar(conn)
        else:
            conn.executescript(SCHEMA)
            conn.commit()
            migrar(conn)
        _INICIALIZADA = True
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
                    fecha_instalacion: str = "", proveedor: str = "",
                    titular_pago: str = "") -> None:
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
                         ("titular_pago", titular_pago),
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
        # si estaba borrado y se vuelve a agregar, queda como agregado
        estado_actual = conn.execute("SELECT COALESCE(estado, 'agregado') AS e FROM contratos"
                                    " WHERE nic = ?", (nic,)).fetchone()
        if estado_actual and estado_actual["e"] == "borrado":
            poner("estado", "agregado")
            poner("borrado_at", None)
            poner("activo", 1)
        poner("updated_at", ahora())
        valores.append(nic)
        conn.execute(f"UPDATE contratos SET {', '.join(campos)} WHERE nic = ?", valores)
    else:
        conn.execute(
            """INSERT INTO contratos
               (nic, razon_social, nombre_normalizado, alias, asesor, dia_corte, activo, notas,
                direccion, telefono, latitud, longitud, paneles, potencia_paneles, inversores,
                bateria, capacidad_bateria, fecha_instalacion, proveedor, estado, titular_pago)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'agregado',?)""",
            (nic, razon_social.strip(), norm, alias.strip(), asesor.strip(),
             int(dia_corte) if dia_corte is not None else None,
             1 if activo is None else int(activo), notas,
             direccion.strip(), telefono.strip(), latitud, longitud, paneles.strip(),
             potencia_paneles, inversores.strip(), bateria.strip(), capacidad_bateria,
             fecha_instalacion.strip(), (proveedor or "edenorte").strip().lower(),
             (titular_pago or "").strip()),
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
                    "titular_pago",
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


DIAS_PAPELERA = 30


# --------------------------------------------------------------- cotizaciones
# Ya se hicieron 12 cotizaciones antes de este modulo: la proxima es la 13.
NUMERO_INICIAL_COTIZACION = 12


def siguiente_numero_cotizacion(conn) -> str:
    """Numero correlativo: sigue desde el mayor guardado (o desde el 12 historico)."""
    fila = conn.execute(
        "SELECT MAX(CAST(numero AS INTEGER)) AS m FROM cotizaciones").fetchone()
    ultimo = int(fila["m"]) if fila and fila["m"] else 0
    return f"{max(ultimo, NUMERO_INICIAL_COTIZACION) + 1:06d}"


TIPOS_DOCUMENTO = ("RNC", "Cédula")


def _tipo_documento(valor) -> str:
    """Normaliza el tipo de documento: RNC o Cédula (por defecto RNC)."""
    texto = str(valor or "").strip().lower()
    if texto.startswith(("ced", "céd", "ci")):
        return "Cédula"
    return "RNC"


def guardar_cotizacion(conn, datos: dict) -> int:
    """Inserta la cotizacion (los renglones van como JSON) y devuelve su id."""
    import json as _json

    items = _json.dumps(datos.get("items") or [], ensure_ascii=False)
    cur = conn.execute(
        """INSERT INTO cotizaciones
           (numero, fecha, cliente_nombre, cliente_rnc, cliente_doc_tipo, cliente_direccion,
            moneda, items, subtotal, itbis, comision_pct, comision, total, notas, firma,
            firma_id, token, creado_por, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (str(datos.get("numero") or ""), str(datos.get("fecha") or hoy()),
         str(datos.get("cliente_nombre") or ""), str(datos.get("cliente_rnc") or ""),
         _tipo_documento(datos.get("cliente_doc_tipo")),
         str(datos.get("cliente_direccion") or ""),
         str(datos.get("moneda") or "Pesos Dominicanos"), items,
         float(datos.get("subtotal") or 0), float(datos.get("itbis") or 0),
         float(datos.get("comision_pct") or 0), float(datos.get("comision") or 0),
         float(datos.get("total") or 0), str(datos.get("notas") or ""),
         str(datos.get("firma") or ""),
         (int(datos["firma_id"]) if datos.get("firma_id") else None),
         (str(datos.get("token") or "").strip() or None),
         str(datos.get("creado_por") or ""), ahora()))
    conn.commit()
    return int(cur.lastrowid or 0)


def _cotizacion_dict(fila) -> dict:
    import json as _json

    datos = dict(fila)
    try:
        datos["items"] = _json.loads(datos.get("items") or "[]")
    except ValueError:
        datos["items"] = []
    return datos


def lista_cotizaciones(conn, limite: int = 200) -> list[dict]:
    filas = conn.execute(
        """SELECT * FROM cotizaciones ORDER BY id DESC LIMIT ?""", (limite,)).fetchall()
    return [_cotizacion_dict(f) for f in filas]


def guardar_firma(conn, datos: dict) -> int:
    """Registra una firma o sello subido (PNG/JPG) con su posición en el PDF."""
    conn.execute(
        """INSERT INTO firmas (nombre, cargo, archivo, ancho, x, y, activo, created_at)
           VALUES (?,?,?,?,?,?,1,?)""",
        (str(datos.get("nombre") or ""), str(datos.get("cargo") or ""),
         str(datos.get("archivo") or ""), float(datos.get("ancho") or 160),
         (float(datos["x"]) if datos.get("x") is not None else None),
         (float(datos["y"]) if datos.get("y") is not None else None),
         ahora()))
    fila = conn.execute("SELECT id FROM firmas ORDER BY id DESC LIMIT 1").fetchone()
    conn.commit()
    return int(fila["id"] if isinstance(fila, dict) else fila[0])


def lista_firmas(conn) -> list[dict]:
    filas = conn.execute("SELECT * FROM firmas WHERE activo = 1 ORDER BY id").fetchall()
    return [dict(f) for f in filas]


def obtener_firma(conn, firma_id: int) -> dict | None:
    fila = conn.execute("SELECT * FROM firmas WHERE id = ?", (int(firma_id),)).fetchone()
    return dict(fila) if fila else None


def ubicar_firma(conn, firma_id: int, x: float, y: float, ancho: float) -> None:
    """Guarda dónde va la firma en la hoja (puntos PDF) para reusarla."""
    conn.execute("UPDATE firmas SET x = ?, y = ?, ancho = ? WHERE id = ?",
                 (float(x), float(y), float(ancho), int(firma_id)))
    conn.commit()


def eliminar_firma(conn, firma_id: int) -> None:
    conn.execute("UPDATE firmas SET activo = 0 WHERE id = ?", (int(firma_id),))
    conn.commit()


def cotizacion_por_token(conn, token: str) -> dict | None:
    """Busca la cotización guardada con ese envío (evita duplicados)."""
    token = (token or "").strip()
    if not token:
        return None
    fila = conn.execute("SELECT * FROM cotizaciones WHERE token = ?", (token,)).fetchone()
    return _cotizacion_dict(fila) if fila else None


def obtener_cotizacion(conn, cotizacion_id: int) -> dict | None:
    fila = conn.execute("SELECT * FROM cotizaciones WHERE id = ?",
                        (cotizacion_id,)).fetchone()
    return _cotizacion_dict(fila) if fila else None


def eliminar_cotizacion(conn, cotizacion_id: int) -> None:
    conn.execute("DELETE FROM cotizaciones WHERE id = ?", (cotizacion_id,))
    conn.commit()


def facturas_reenviables(conn) -> dict[str, list[dict]]:
    """Facturas que se pueden reenviar, agrupadas por NIC (la mas nueva primero).

    Alimenta el desplegable de «Reenviar facturas»: ahi se elige una factura
    concreta, no solo la ultima del cliente.
    """
    filas = conn.execute(
        """SELECT id, nic, corte, periodo_ini, periodo_fin, kwh, facturado,
                  COALESCE(enviada, 0) AS enviada, estado
           FROM facturas
           WHERE estado IN ('descargada', 'descartada')
           ORDER BY nic, corte DESC, id DESC""").fetchall()
    salida: dict[str, list[dict]] = {}
    for f in filas:
        datos = dict(f)
        salida.setdefault(str(datos["nic"]), []).append(datos)
    return salida


def clientes_con_ultima_factura(conn) -> list[dict]:
    """Todos los clientes con su factura mas reciente (para el modulo Reenviar)."""
    filas = conn.execute(
        """SELECT c.nic, c.razon_social, c.nombre_normalizado, c.alias, c.asesor,
                  COALESCE(c.proveedor, 'edenorte') AS proveedor,
                  f.id AS factura_id, f.corte, f.facturado, f.estado AS estado_factura,
                  f.enviada, COALESCE(f.tg_file_id, '') AS tg_file_id
             FROM contratos c
             LEFT JOIN facturas f ON f.id = (
                  SELECT id FROM facturas
                   WHERE nic = c.nic AND estado IN ('descargada', 'descartada')
                   ORDER BY corte DESC, id DESC LIMIT 1)
            WHERE COALESCE(c.estado, 'agregado') <> 'borrado' AND c.activo = 1
            ORDER BY c.nombre_normalizado, c.nic""").fetchall()
    return [dict(f) for f in filas]


def contratos_papelera(conn) -> list[dict]:
    """Clientes en la papelera, con cuantos dias les quedan."""
    filas = conn.execute(
        """SELECT nic, razon_social, proveedor, borrado_at, notas
           FROM contratos WHERE estado = 'borrado' ORDER BY borrado_at DESC""").fetchall()
    from datetime import datetime as _dt

    salida = []
    for f in filas:
        fila = dict(f)
        dias = None
        if fila.get("borrado_at"):
            try:
                cuando = _dt.fromisoformat(str(fila["borrado_at"])[:19])
                dias = max(0, DIAS_PAPELERA - (_dt.now() - cuando).days)
            except ValueError:
                dias = DIAS_PAPELERA
        fila["dias_restantes"] = dias
        salida.append(fila)
    return salida


def purgar_papelera(conn, dias: int = DIAS_PAPELERA) -> list[str]:
    """Borra DEFINITIVAMENTE lo que lleva mas de `dias` en la papelera.

    Se lleva todo lo del cliente: contrato, facturas, pendientes y proyectos.
    Devuelve los NIC purgados.
    """
    from datetime import datetime as _dt

    limite = _dt.now().timestamp() - dias * 86400
    purgados = []
    for fila in conn.execute(
            "SELECT nic, borrado_at FROM contratos WHERE estado = 'borrado'").fetchall():
        nic = str(fila["nic"])
        cuando = fila["borrado_at"]
        if not cuando:
            # sin fecha no se puede saber la antiguedad: se sella ahora y se espera.
            # (antes se purgaba de inmediato: se perdieron clientes recien eliminados)
            conn.execute("UPDATE contratos SET borrado_at = ? WHERE nic = ?", (ahora(), nic))
            continue
        try:
            viejo = _dt.fromisoformat(str(cuando)[:19]).timestamp() <= limite
        except ValueError:
            conn.execute("UPDATE contratos SET borrado_at = ? WHERE nic = ?", (ahora(), nic))
            continue
        if not viejo:
            continue
        for tabla in ("facturas", "pendientes", "proyectos"):
            conn.execute(f"DELETE FROM {tabla} WHERE nic = ?", (nic,))
        conn.execute("DELETE FROM contratos WHERE nic = ?", (nic,))
        purgados.append(nic)
    if purgados:
        log_evento(conn, "aviso",
                   f"Papelera: {len(purgados)} cliente(s) eliminados definitivamente",
                   ", ".join(purgados)[:200])
    conn.commit()
    return purgados


def listar_contratos(conn: sqlite3.Connection, solo_activos: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM contratos WHERE COALESCE(estado, 'agregado') <> 'borrado'"
    if solo_activos:
        sql += " AND activo = 1"
    sql += " ORDER BY nic"
    return conn.execute(sql).fetchall()


def obtener_contrato(conn: sqlite3.Connection, nic: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM contratos WHERE nic = ?", (str(nic),)).fetchone()


def eliminar_contrato(conn, nic: str, motivo: str = "") -> None:
    """Marca el cliente como 'borrado' sin perder su historial de facturas.

    Antes se hacia DELETE: se perdia el rastro de que ese NIC existio y por que
    desaparecio. Ahora queda la fila con estado='borrado' (y activo=0) para que
    no se liste ni se procese.
    """
    nic = str(nic).strip()
    nota = (f"eliminado {ahora()}" + (f": {motivo}" if motivo else ""))[:200]
    conn.execute(
        "UPDATE contratos SET estado = 'borrado', activo = 0, borrado_at = ?, updated_at = ?, "
        "notas = CASE WHEN COALESCE(notas, '') = '' THEN ? ELSE notas END "
        "WHERE nic = ?", (ahora(), ahora(), nota, nic))
    # sin contrato no tiene sentido seguir reintentando sus pendientes
    conn.execute("DELETE FROM pendientes WHERE nic = ?", (nic,))
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


def necesita_pdf(conn, ref_pago: str) -> bool:
    """True si esa factura esta en la base pero sin archivo (hay que bajarlo)."""
    fila = conn.execute("SELECT estado, COALESCE(pdf_path, '') AS pdf FROM facturas"
                        " WHERE ref_pago = ?", (str(ref_pago),)).fetchone()
    if fila is None:
        return True
    return fila["estado"] in (None, "", "sin_pdf") or not fila["pdf"]


def estado_factura(conn, ref_pago: str) -> str | None:
    """Estado guardado de esa referencia (None si no esta en la base)."""
    fila = conn.execute("SELECT estado FROM facturas WHERE ref_pago = ?",
                        (str(ref_pago),)).fetchone()
    return fila["estado"] if fila else None


def facturas_sin_pdf(conn, nic: str) -> list:
    """Facturas que el portal lista pero cuyo PDF ya no entrega."""
    return conn.execute(
        """SELECT * FROM facturas
           WHERE nic = ? AND estado = 'sin_pdf'
           ORDER BY emision DESC, id DESC""", (str(nic),)).fetchall()


def factura_existe(conn: sqlite3.Connection, ref_pago: str) -> bool:
    return conn.execute("SELECT 1 FROM facturas WHERE ref_pago = ?",
                        (str(ref_pago),)).fetchone() is not None


def facturas_del_dia(conn: sqlite3.Connection, fecha: str | None = None) -> list[sqlite3.Row]:
    fecha = fecha or hoy()
    return conn.execute(
        "SELECT * FROM facturas WHERE corte = ? AND estado = 'descargada' ORDER BY nic", (fecha,)
    ).fetchall()


def facturas_sin_enviar(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM facturas WHERE enviada = 0 AND estado = 'descargada' ORDER BY nic"
    ).fetchall()


def marcar_enviadas(conn: sqlite3.Connection, ids: list[int], chat_id: str,
                    detalles: dict[int, dict] | None = None) -> None:
    """Marca como enviadas y guarda el file_id/message_id de Telegram.

    Con el file_id el panel puede volver a mostrar el PDF aunque el archivo ya no
    este en disco (se borra despues de enviarlo).
    """
    if not ids:
        return
    marcas = ",".join("?" for _ in ids)
    conn.execute(f"UPDATE facturas SET enviada = 1 WHERE id IN ({marcas})", ids)
    for factura_id, datos in (detalles or {}).items():
        file_id = (datos or {}).get("file_id") or ""
        message_id = (datos or {}).get("message_id")
        if file_id:
            conn.execute(
                "UPDATE facturas SET tg_file_id = ?, tg_message_id = ? WHERE id = ?",
                (file_id, str(message_id) if message_id else "", factura_id))
    conn.commit()


def asesores(conn) -> list[dict]:
    """Asesores que ya se usaron alguna vez, con cuantos clientes tiene cada uno."""
    filas = conn.execute(
        """SELECT TRIM(asesor) AS asesor, COUNT(*) AS clientes
           FROM contratos
           WHERE TRIM(COALESCE(asesor, '')) <> ''
           GROUP BY TRIM(asesor)
           ORDER BY LOWER(TRIM(asesor))""").fetchall()
    return [dict(f) for f in filas]


def factura_ya_enviada(conn: sqlite3.Connection, ref_pago: str) -> bool:
    """True si esa factura ya salio al grupo (por marca o por tener file_id)."""
    fila = conn.execute(
        "SELECT enviada, tg_file_id FROM facturas WHERE ref_pago = ?", (str(ref_pago),)
    ).fetchone()
    if not fila:
        return False
    return bool(fila["enviada"]) or bool(fila["tg_file_id"] or "")


def ultima_factura(conn: sqlite3.Connection, nic: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM facturas WHERE nic = ? ORDER BY corte DESC, emision DESC LIMIT 1",
        (str(nic),)
    ).fetchone()


def historico_facturas(conn: sqlite3.Connection, nic: str) -> list[sqlite3.Row]:
    """Todas las facturas descargadas de un contrato, la mas reciente primero."""
    # descartada / sin_consumo tambien son facturas reales del cliente: si tienen
    # PDF se muestran (antes se escondian y el historial quedaba incompleto)
    return conn.execute(
        """SELECT * FROM facturas
           WHERE nic = ? AND estado IN ('descargada', 'descartada')
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
                  SUM(CASE WHEN dia_corte IS NULL OR dia_corte = 0 THEN 1 ELSE 0 END) AS sin_corte
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
def log_evento(conn: sqlite3.Connection, nivel: str, mensaje: str, detalle: str = "",
               usuario: str | None = None) -> None:
    """Anota un movimiento. Sin `usuario` se usa el de la peticion en curso."""
    quien = usuario if usuario is not None else usuario_en_curso.get()
    # la fecha va explicita: el DEFAULT del motor es UTC y descuadraba 4 horas
    conn.execute("INSERT INTO eventos (nivel, mensaje, detalle, usuario, fecha)"
                 " VALUES (?,?,?,?,?)",
                 (nivel, mensaje, detalle, (quien or "")[:60], ahora()))
    conn.commit()


def eventos_pagina(conn: sqlite3.Connection, usuario: str = "", texto: str = "",
                   nivel: str = "", limite: int = 100, offset: int = 0) -> list[sqlite3.Row]:
    """Movimientos filtrados, del mas nuevo al mas viejo."""
    sql = "SELECT id, fecha, nivel, mensaje, detalle, usuario FROM eventos WHERE 1=1"
    params: list = []
    if usuario:
        sql += " AND usuario = ?"
        params.append(usuario)
    if nivel:
        sql += " AND nivel = ?"
        params.append(nivel)
    if texto:
        sql += " AND (mensaje LIKE ? OR detalle LIKE ?)"
        params += [f"%{texto}%", f"%{texto}%"]
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limite, offset]
    return conn.execute(sql, params).fetchall()


def contar_eventos(conn: sqlite3.Connection, usuario: str = "", texto: str = "",
                   nivel: str = "") -> int:
    sql = "SELECT COUNT(*) AS n FROM eventos WHERE 1=1"
    params: list = []
    if usuario:
        sql += " AND usuario = ?"
        params.append(usuario)
    if nivel:
        sql += " AND nivel = ?"
        params.append(nivel)
    if texto:
        sql += " AND (mensaje LIKE ? OR detalle LIKE ?)"
        params += [f"%{texto}%", f"%{texto}%"]
    return conn.execute(sql, params).fetchone()["n"]


def usuarios_de_eventos(conn: sqlite3.Connection) -> list[str]:
    filas = conn.execute(
        "SELECT usuario, COUNT(*) AS n FROM eventos WHERE usuario <> '' "
        "GROUP BY usuario ORDER BY n DESC").fetchall()
    return [f["usuario"] for f in filas]


def ultimos_eventos(conn: sqlite3.Connection, limite: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM eventos ORDER BY id DESC LIMIT ?", (limite,)
    ).fetchall()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Inicializa la base de datos del proyecto")
    ap.add_argument("--init", action="store_true", help="crea las tablas (SQLite o Postgres)")
    ap.add_argument("--estado", action="store_true", help="muestra el motor y los conteos")
    ap.add_argument("--importar", action="store_true",
                    help="copia los datos del SQLite local a la base configurada")
    args = ap.parse_args()

    motor = "postgres" if pg.es_postgres(pg.limpiar_url(config.DATABASE_URL)) else "sqlite"
    if args.estado:
        print(f"motor: {motor}")
        if motor == "sqlite":
            print(f"archivo: {config.DB_PATH}")
    if args.importar:
        if motor != "postgres":
            raise SystemExit("Para importar hay que configurar DATABASE_URL (Postgres)")
        init_db()
        origen = sqlite3.connect(config.DB_PATH)
        origen.row_factory = sqlite3.Row
        tablas = ("contratos", "facturas", "pendientes", "envios",
                  "usuarios", "proyectos", "eventos")
        with sesion() as destino:
            for tabla in tablas:
                filas = origen.execute(f"SELECT * FROM {tabla}").fetchall()
                if not filas:
                    continue
                destino.execute(f"DELETE FROM {tabla}")     # destino limpio
                # solo las columnas que existen en el destino (evita restos locales)
                sql_cols = destino.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = ?", (tabla,)).fetchall()
                en_destino = {f["column_name"] for f in sql_cols}
                cols = [c for c in filas[0].keys() if c in en_destino]
                sql = (f"INSERT INTO {tabla} ({','.join(cols)}) "
                       f"VALUES ({','.join('?' for _ in cols)})")
                for fila in filas:
                    destino.execute(sql, [fila[c] for c in cols])
                print(f"  {tabla:12} {len(filas)} filas copiadas")
            # al copiar ids explicitos la secuencia queda atrasada: se recoloca
            for tabla in ("facturas", "pendientes", "envios", "usuarios",
                          "proyectos", "eventos"):
                destino.execute(
                    f"SELECT setval(pg_get_serial_sequence('{tabla}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {tabla}), 1))")
            destino.commit()
        origen.close()
        print("importacion terminada")
        raise SystemExit(0)

    if args.init or not args.estado:
        init_db(forzar=True)
        print(f"base inicializada ({motor})")
    with sesion() as conn:
        for tabla in ("contratos", "facturas", "pendientes", "envios",
                      "usuarios", "proyectos", "eventos"):
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {tabla}").fetchone()["n"]
            print(f"  {tabla:12} {n}")
