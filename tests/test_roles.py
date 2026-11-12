"""Roles del panel: admin, soporte y asesor, y protección del admin principal.

El principal es el ADMIN_USER del .env (hoy `enpower`): nadie más lo desactiva, le
cambia la clave ni puede crear un usuario con ese nombre.

Se prueban las funciones de ruta directamente (sin cliente HTTP) para no meter
dependencias nuevas: el TestClient de starlette hoy exige httpx.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import auth, config, dashboard, db


class Peticion:
    """Lo mínimo que las rutas leen del request."""

    def __init__(self, usuario=None):
        self.state = SimpleNamespace(usuario=usuario)
        self.cookies = {}
        self.headers = {}


def _crear(usuario: str, rol: str, clave: str = "ClaveSegura123") -> int:
    with db.sesion() as conn:
        return auth.crear_usuario(conn, usuario, clave, nombre=usuario.title(), rol=rol)


def _usuario(nombre: str, clave: str = "ClaveSegura123") -> auth.Usuario:
    with db.sesion() as conn:
        return auth.autenticar(conn, nombre, clave)


def _id(nombre: str) -> int:
    with db.sesion() as conn:
        return conn.execute("SELECT id FROM usuarios WHERE usuario = ?",
                            (nombre,)).fetchone()["id"]


@pytest.fixture(autouse=True)
def principal(db_path, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_USER", "principal")
    monkeypatch.setattr(config, "SECRET_KEY", "clave-de-prueba-para-firmar")
    _crear("principal", "admin", "ClavePrincipal123")
    return "principal"


# ------------------------------------------------------------------ propiedades
def test_propiedades_de_cada_rol():
    admin = auth.Usuario(id=1, usuario="a", nombre="A", rol="admin", activo=1)
    soporte = auth.Usuario(id=2, usuario="s", nombre="S", rol="soporte", activo=1)
    asesor = auth.Usuario(id=3, usuario="x", nombre="X", rol="asesor", activo=1)

    assert admin.es_admin and admin.puede_operar
    assert soporte.es_soporte and soporte.puede_operar and not soporte.es_admin
    assert not asesor.puede_operar and not asesor.es_admin


def test_roles_declarados():
    assert set(auth.ROLES) == {"admin", "soporte", "asesor"}
    assert set(auth.ROLES_OPERADOR) == {"admin", "soporte"}


# ------------------------------------------------------- guardas de las rutas
def test_requiere_operador_deja_pasar_a_soporte_y_admin():
    _crear("soporte1", "soporte")
    _crear("admin1", "admin")

    dashboard.requiere_operador(Peticion(_usuario("soporte1")))   # no lanza
    dashboard.requiere_operador(Peticion(_usuario("admin1")))     # no lanza


def test_requiere_operador_frena_al_asesor_y_al_anonimo():
    _crear("asesor1", "asesor")

    with pytest.raises(HTTPException) as asesor:
        dashboard.requiere_operador(Peticion(_usuario("asesor1")))
    assert asesor.value.status_code == 403

    with pytest.raises(HTTPException) as anonimo:
        dashboard.requiere_operador(Peticion(None))
    assert anonimo.value.status_code == 401


def test_requiere_admin_frena_al_soporte():
    _crear("soporte1", "soporte")
    with pytest.raises(HTTPException) as exc:
        dashboard.requiere_admin(Peticion(_usuario("soporte1")))
    assert exc.value.status_code == 403


# ------------------------------------------------------ admin principal protegido
def test_el_principal_esta_marcado():
    assert dashboard.es_admin_principal("principal")
    assert dashboard.es_admin_principal("PRINCIPAL ")
    assert not dashboard.es_admin_principal("admin2")


def test_nadie_desactiva_al_principal():
    _crear("admin2", "admin")
    peticion = Peticion(_usuario("admin2"))

    with pytest.raises(HTTPException) as exc:
        dashboard.usuarios_estado(peticion, _id("principal"), activo="0")
    assert exc.value.status_code == 400

    with db.sesion() as conn:
        assert conn.execute("SELECT activo FROM usuarios WHERE usuario = 'principal'"
                            ).fetchone()["activo"] == 1


def test_nadie_cambia_la_clave_del_principal():
    _crear("admin2", "admin")
    peticion = Peticion(_usuario("admin2"))

    with pytest.raises(HTTPException) as exc:
        dashboard.usuarios_password(peticion, _id("principal"), password="OtraClave123")
    assert exc.value.status_code == 400

    with db.sesion() as conn:
        assert auth.autenticar(conn, "principal", "ClavePrincipal123") is not None


def test_no_se_puede_crear_un_usuario_con_el_nombre_del_principal():
    _crear("admin2", "admin")
    peticion = Peticion(_usuario("admin2"))

    with pytest.raises(HTTPException) as exc:
        dashboard.usuarios_crear(peticion, usuario_nuevo="principal",
                                 password="IntentoDeRobo123", nombre="impostor",
                                 rol="asesor")
    assert exc.value.status_code == 400

    with db.sesion() as conn:
        fila = conn.execute("SELECT rol, nombre FROM usuarios WHERE usuario = 'principal'"
                            ).fetchone()
    assert fila["rol"] == "admin"
    assert fila["nombre"] != "impostor"


# ------------------------------------------------------------------ alta de roles
def test_admin_crea_un_soporte():
    _crear("admin1", "admin")
    peticion = Peticion(_usuario("admin1"))

    respuesta = dashboard.usuarios_crear(peticion, usuario_nuevo="soporte9",
                                         password="ClaveSegura123", nombre="Soporte 9",
                                         rol="soporte")
    assert respuesta.status_code == 303

    with db.sesion() as conn:
        assert conn.execute("SELECT rol FROM usuarios WHERE usuario = 'soporte9'"
                            ).fetchone()["rol"] == "soporte"


def test_un_rol_inventado_se_rechaza():
    _crear("admin1", "admin")
    peticion = Peticion(_usuario("admin1"))

    with pytest.raises(HTTPException) as exc:
        dashboard.usuarios_crear(peticion, usuario_nuevo="raro",
                                 password="ClaveSegura123", nombre="Raro",
                                 rol="superadmin")
    assert exc.value.status_code == 400


def test_asesor_no_crea_usuarios():
    _crear("asesor1", "asesor")
    peticion = Peticion(_usuario("asesor1"))

    with pytest.raises(HTTPException) as exc:
        dashboard.usuarios_crear(peticion, usuario_nuevo="colado",
                                 password="ClaveSegura123", nombre="Colado",
                                 rol="admin")
    assert exc.value.status_code == 403
