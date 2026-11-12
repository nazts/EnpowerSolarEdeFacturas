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


# ------------------------------------------- soporte tambien opera con clientes
def test_soporte_puede_abrir_el_alta_de_cliente(monkeypatch):
    _crear("soporte1", "soporte")
    dashboard.cliente_nuevo(Peticion(_usuario("soporte1")))      # no lanza


def test_asesor_no_abre_el_alta_de_cliente():
    _crear("asesor1", "asesor")
    with pytest.raises(HTTPException) as exc:
        dashboard.cliente_nuevo(Peticion(_usuario("asesor1")))
    assert exc.value.status_code == 403


def test_soporte_crea_un_cliente(monkeypatch):
    from app import runner

    # el alta dispara una descarga en segundo plano: aqui no hace falta
    monkeypatch.setattr(runner, "sincronizar_historial", lambda **kw: {"ok": True})
    _crear("soporte1", "soporte")

    # el middleware del panel deja el usuario en el contextvar: se simula aqui
    marca = db.usuario_en_curso.set("soporte1")
    try:
        respuesta = dashboard.crear_cliente(Peticion(_usuario("soporte1")), nic="8877665",
                                           razon_social="PRUEBA SOPORTE", proveedor="edenorte",
                                           referencia="", alias="", asesor="", dia_corte="",
                                           notas="", direccion="", telefono="", latitud="",
                                           longitud="", paneles="", potencia_paneles="",
                                           inversores="", bateria="", capacidad_bateria="",
                                           fecha_instalacion="")
    finally:
        db.usuario_en_curso.reset(marca)
    assert respuesta.status_code == 303
    with db.sesion() as conn:
        assert db.obtener_contrato(conn, "8877665") is not None
        quien = conn.execute("SELECT usuario FROM eventos WHERE mensaje LIKE '%8877665%'"
                             ).fetchone()["usuario"]
    assert quien == "soporte1"


def test_asesor_no_crea_clientes():
    _crear("asesor1", "asesor")
    with pytest.raises(HTTPException) as exc:
        dashboard.crear_cliente(Peticion(_usuario("asesor1")), nic="1122334",
                                razon_social="X", proveedor="edenorte", referencia="",
                                alias="", asesor="", dia_corte="", notas="", direccion="",
                                telefono="", latitud="", longitud="", paneles="",
                                potencia_paneles="", inversores="", bateria="",
                                capacidad_bateria="", fecha_instalacion="")
    assert exc.value.status_code == 403


def test_soporte_edita_la_ficha_y_el_equipo(monkeypatch):
    from app import runner
    monkeypatch.setattr(runner, "sincronizar_historial", lambda **kw: {"ok": True})
    _crear("soporte1", "soporte")
    with db.sesion() as conn:
        db.upsert_contrato(conn, "5550001", razon_social="CLIENTE PRUEBA")

    peticion = Peticion(_usuario("soporte1"))
    assert dashboard.cliente_editar(peticion, "5550001").status_code == 200

    respuesta = dashboard.guardar_equipo(peticion, "5550001", direccion="Calle 1",
                                         telefono="809-000-0000", latitud="", longitud="",
                                         paneles="12", potencia_paneles="",
                                         inversores="", bateria="", capacidad_bateria="",
                                         fecha_instalacion="")
    assert respuesta.status_code == 303
    with db.sesion() as conn:
        assert db.obtener_contrato(conn, "5550001")["direccion"] == "Calle 1"


def test_asesor_no_edita_la_ficha():
    _crear("asesor1", "asesor")
    with db.sesion() as conn:
        db.upsert_contrato(conn, "5550002", razon_social="OTRO")
    with pytest.raises(HTTPException) as exc:
        dashboard.cliente_editar(Peticion(_usuario("asesor1")), "5550002")
    assert exc.value.status_code == 403


# ------------------------------------------------------- registro de actividad
def test_log_evento_guarda_quien_lo_hizo():
    marca = db.usuario_en_curso.set("soporte2")
    try:
        with db.sesion() as conn:
            db.log_evento(conn, "info", "Prueba de auditoria")
    finally:
        db.usuario_en_curso.reset(marca)

    with db.sesion() as conn:
        fila = conn.execute("SELECT usuario FROM eventos WHERE mensaje = 'Prueba de auditoria'"
                            ).fetchone()
    assert fila["usuario"] == "soporte2"


def test_log_evento_acepta_usuario_explicito():
    with db.sesion() as conn:
        db.log_evento(conn, "info", "Prueba con nombre", usuario="admin3")
    with db.sesion() as conn:
        assert conn.execute("SELECT usuario FROM eventos WHERE mensaje = 'Prueba con nombre'"
                            ).fetchone()["usuario"] == "admin3"


def test_actividad_filtra_y_pagina():
    with db.sesion() as conn:
        db.log_evento(conn, "info", "Alta de cliente 123", usuario="soporte1")
        db.log_evento(conn, "aviso", "Fallo al descargar", usuario="enpower")
        db.log_evento(conn, "info", "Alta de cliente 456", usuario="soporte1")

        todos = db.contar_eventos(conn)
        assert todos >= 3

        assert db.contar_eventos(conn, usuario="soporte1") == 2
        de_soporte = db.eventos_pagina(conn, usuario="soporte1")
        assert [e["mensaje"] for e in de_soporte] == ["Alta de cliente 456", "Alta de cliente 123"]

        # el mas nuevo primero y el limite respeta el offset
        primera = db.eventos_pagina(conn, limite=1)
        segunda = db.eventos_pagina(conn, limite=1, offset=1)
        assert primera[0]["id"] > segunda[0]["id"]

        assert db.contar_eventos(conn, texto="456") == 1
        assert db.contar_eventos(conn, nivel="aviso") == 1
        assert "soporte1" in db.usuarios_de_eventos(conn)


def test_solo_admin_ve_la_actividad():
    _crear("soporte1", "soporte")
    with pytest.raises(HTTPException) as soporte:
        dashboard.requiere_admin(Peticion(_usuario("soporte1")))
    assert soporte.value.status_code == 403

    dashboard.requiere_admin(Peticion(_usuario("principal", "ClavePrincipal123")))
