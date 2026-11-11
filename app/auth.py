"""Sistema de usuarios: hash de contrasenas, sesiones firmadas y permisos."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass

from . import config

ITERACIONES = 200_000
DURACION_SESION = 60 * 60 * 10  # 10 horas


# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    sal = secrets.token_bytes(16)
    clave = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sal, ITERACIONES)
    return "pbkdf2${}${}${}".format(
        ITERACIONES,
        base64.b64encode(sal).decode(),
        base64.b64encode(clave).decode(),
    )


def verificar_password(password: str, guardado: str) -> bool:
    try:
        _, iteraciones, sal_b64, clave_b64 = guardado.split("$")
        sal = base64.b64decode(sal_b64)
        esperado = base64.b64decode(clave_b64)
        calculado = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                        sal, int(iteraciones))
        return hmac.compare_digest(esperado, calculado)
    except Exception:
        return False


# --------------------------------------------------------------------- tokens
def _clave() -> bytes:
    return config.SECRET_KEY.encode("utf-8")


def crear_token(usuario_id: int) -> str:
    expira = int(time.time()) + DURACION_SESION
    carga = f"{usuario_id}:{expira}"
    firma = hmac.new(_clave(), carga.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{carga}:{firma}".encode()).decode()


def leer_token(token: str | None) -> int | None:
    if not token:
        return None
    try:
        crudo = base64.urlsafe_b64decode(token.encode()).decode()
        usuario_id, expira, firma = crudo.split(":")
        carga = f"{usuario_id}:{expira}"
        esperado = hmac.new(_clave(), carga.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(firma, esperado):
            return None
        if int(expira) < int(time.time()):
            return None
        return int(usuario_id)
    except Exception:
        return None


# -------------------------------------------------------------------- usuarios
@dataclass
class Usuario:
    id: int
    usuario: str
    nombre: str
    rol: str
    activo: int

    @property
    def es_admin(self) -> bool:
        return self.rol == "admin"


def crear_usuario(conn: sqlite3.Connection, usuario: str, password: str,
                  nombre: str = "", rol: str = "asesor") -> int:
    cursor = conn.execute(
        """INSERT INTO usuarios (usuario, password_hash, nombre, rol)
           VALUES (?,?,?,?)
           ON CONFLICT(usuario) DO UPDATE SET password_hash = excluded.password_hash,
                                              nombre = excluded.nombre,
                                              rol = excluded.rol,
                                              activo = 1""",
        (usuario.strip().lower(), hash_password(password), nombre.strip(), rol),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def obtener_usuario(conn: sqlite3.Connection, usuario_id: int) -> Usuario | None:
    fila = conn.execute(
        "SELECT id, usuario, nombre, rol, activo FROM usuarios WHERE id = ?",
        (usuario_id,),
    ).fetchone()
    return Usuario(**dict(fila)) if fila else None


def autenticar(conn: sqlite3.Connection, usuario: str, password: str) -> Usuario | None:
    fila = conn.execute(
        "SELECT id, usuario, password_hash, nombre, rol, activo FROM usuarios WHERE usuario = ?",
        (usuario.strip().lower(),),
    ).fetchone()
    if not fila or not fila["activo"]:
        return None
    if not verificar_password(password, fila["password_hash"]):
        return None
    return Usuario(id=fila["id"], usuario=fila["usuario"], nombre=fila["nombre"],
                   rol=fila["rol"], activo=fila["activo"])


def listar_usuarios(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT id, usuario, nombre, rol, activo, creado_en, ultimo_acceso
           FROM usuarios ORDER BY rol, usuario"""
    ).fetchall()


def cambiar_password(conn: sqlite3.Connection, usuario_id: int, password: str) -> None:
    conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                 (hash_password(password), usuario_id))
    conn.commit()


def marcar_acceso(conn: sqlite3.Connection, usuario_id: int) -> None:
    conn.execute("UPDATE usuarios SET ultimo_acceso = datetime('now','localtime') WHERE id = ?",
                 (usuario_id,))
    conn.commit()


def asegurar_admin_inicial(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """Crea el primer administrador si no existe ningun usuario.

    Devuelve (usuario, password) cuando lo crea, para poder informarlo una vez.
    """
    total = conn.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()["n"]
    if total:
        return None
    usuario = config.ADMIN_USER or "admin"
    password = config.ADMIN_PASSWORD or secrets.token_urlsafe(9)
    crear_usuario(conn, usuario, password, nombre="Administrador", rol="admin")
    return usuario, password
