"""Alta de clientes: buscar no debe guardar, cancelar debe deshacer.

El fallo real: al buscar el NIC en el alta, el contrato se asociaba en el portal
y **se guardaba en la base** antes de que el usuario confirmara. Si cancelaba,
el cliente quedaba creado (y sus pendientes generaban avisos de corte). Ahora:
la búsqueda solo asocia y devuelve datos; el guardado lo hace el formulario, y
cancelar quita el contrato de la cuenta del portal.

Además, eliminar un cliente ya no borra la fila: la deja con estado='borrado'.
"""
from types import SimpleNamespace

import pytest

from app import dashboard, db


class _PortalFalso:
    def __init__(self, en_la_cuenta=()):
        self.en_la_cuenta = list(en_la_cuenta)
        self.asociados = []
        self.quitados = []
        self.cerrado = False

    def login(self):
        return None

    def cerrar(self):
        self.cerrado = True

    def contratos(self):
        return [SimpleNamespace(nic=n, titular="X", direccion="", telefono="")
                for n in self.en_la_cuenta]

    def asociar_contrato(self, nic, espera_max=60, referencia=None):
        self.asociados.append(nic)
        self.en_la_cuenta.append(nic)
        return SimpleNamespace(nic=nic, titular="JORGE RAMON DE LA CRUZ TAVAREZ",
                               direccion="Calle 12", telefono="809-258-1309")

    def quitar_contrato(self, nic):
        self.quitados.append(nic)
        if nic in self.en_la_cuenta:
            self.en_la_cuenta.remove(nic)
        return True


@pytest.fixture
def portal(monkeypatch):
    from app import proveedores

    p = _PortalFalso()
    monkeypatch.setattr(proveedores, "portal_de", lambda prov: p)
    monkeypatch.setattr(dashboard, "_ASOCIADOS_POR_BUSQUEDA", {})
    return p


def _guardar_contrato(nic="6997885"):
    with db.sesion() as c:
        db.upsert_contrato(c, nic, razon_social="JORGE RAMON DE LA CRUZ TAVAREZ", dia_corte=16)


# --------------------------------------------------- borrado logico (estado)
def test_eliminar_marca_borrado_y_no_pierde_la_fila(db_path):
    _guardar_contrato()
    with db.sesion() as c:
        db.eliminar_contrato(c, "6997885", motivo="prueba")
        fila = c.execute("SELECT nic, estado, activo FROM contratos WHERE nic='6997885'").fetchone()

    assert fila is not None                       # la fila sigue existiendo
    assert fila["estado"] == "borrado"
    assert fila["activo"] == 0


def test_eliminar_quita_los_pendientes(db_path):
    _guardar_contrato()
    with db.sesion() as c:
        c.execute("INSERT INTO pendientes (nic, fecha_corte, primer_intento, ultimo_intento,"
                  " intentos, estado) VALUES ('6997885','2026-11-16','x','x',3,'atrasado')")
        c.commit()
        db.eliminar_contrato(c, "6997885")
        assert c.execute("SELECT COUNT(*) AS n FROM pendientes WHERE nic='6997885'"
                         ).fetchone()["n"] == 0


def test_el_listado_no_muestra_los_borrados(db_path):
    _guardar_contrato("6997885")
    _guardar_contrato("7533709")
    with db.sesion() as c:
        db.eliminar_contrato(c, "6997885")
        assert [r["nic"] for r in db.listar_contratos(c)] == ["7533709"]


def test_volver_a_agregarlo_lo_reactiva(db_path):
    _guardar_contrato()
    with db.sesion() as c:
        db.eliminar_contrato(c, "6997885")
    _guardar_contrato()

    with db.sesion() as c:
        fila = c.execute("SELECT estado, activo FROM contratos WHERE nic='6997885'").fetchone()
        assert fila["estado"] == "agregado" and fila["activo"] == 1
        assert [r["nic"] for r in db.listar_contratos(c)] == ["6997885"]


# ------------------------------------------- la busqueda no guarda el cliente
def test_buscar_el_nic_no_crea_el_cliente(db_path, portal):
    datos = dashboard.api_contrato("6997885", proveedor="edenorte").body
    import json
    carga = json.loads(datos)

    assert carga["ok"] is True and carga["asociado"] is True
    assert portal.asociados == ["6997885"]         # se asocio para poder leerlo
    with db.sesion() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM contratos").fetchone()["n"] == 0


def test_cancelar_quita_el_contrato_de_la_cuenta(db_path, portal):
    dashboard.api_contrato("6997885", proveedor="edenorte")
    assert "6997885" in portal.en_la_cuenta

    respuesta = dashboard.api_contrato_cancelar("6997885", None)

    assert respuesta.status_code == 200
    assert portal.quitados == ["6997885"]          # se deshizo en el portal
    assert portal.en_la_cuenta == []
    assert portal.cerrado is True


def test_cancelar_un_nic_que_ya_estaba_en_la_cuenta_no_lo_quita(db_path, portal):
    portal.en_la_cuenta.append("6997885")           # ya era del usuario
    dashboard.api_contrato("6997885", proveedor="edenorte")

    dashboard.api_contrato_cancelar("6997885", None)

    assert portal.quitados == []                    # no se toca lo que ya existia


# ------------------------------------ eliminar el cliente tambien en el portal
def test_borrar_contrato_lo_quita_del_portal_y_lo_marca(db_path, portal, monkeypatch):
    monkeypatch.setattr(dashboard, "requiere_admin", lambda request: None)
    _guardar_contrato()

    dashboard.borrar_contrato("6997885", None)

    assert portal.quitados == ["6997885"]
    with db.sesion() as c:
        fila = c.execute("SELECT estado FROM contratos WHERE nic='6997885'").fetchone()
    assert fila["estado"] == "borrado"


def test_si_el_portal_falla_el_cliente_igual_queda_borrado(db_path, portal, monkeypatch):
    monkeypatch.setattr(dashboard, "requiere_admin", lambda request: None)
    _guardar_contrato()

    def romper(nic):
        raise RuntimeError("portal caido")

    portal.quitar_contrato = romper
    dashboard.borrar_contrato("6997885", None)

    with db.sesion() as c:
        fila = c.execute("SELECT estado FROM contratos WHERE nic='6997885'").fetchone()
        assert fila["estado"] == "borrado"
        # queda registrado el fallo, no se oculta
        mensajes = [e["mensaje"] for e in c.execute("SELECT mensaje FROM eventos").fetchall()]
    assert any("no confirmo la eliminacion" in m for m in mensajes)


# ------------------------------------------------------------- papelera (30 dias)
def test_la_papelera_lista_los_borrados_con_sus_dias(db_path):
    _guardar_contrato()
    with db.sesion() as c:
        db.eliminar_contrato(c, "6997885")
        papelera = db.contratos_papelera(c)

    assert [f["nic"] for f in papelera] == ["6997885"]
    assert papelera[0]["dias_restantes"] == db.DIAS_PAPELERA
    assert papelera[0]["borrado_at"]


def test_purgar_borra_lo_que_paso_los_30_dias(db_path):
    _guardar_contrato()
    with db.sesion() as c:
        db.guardar_factura(c, {"nic": "6997885", "ref_pago": "R1", "estado": "descargada"})
        db.eliminar_contrato(c, "6997885")
        c.execute("UPDATE contratos SET borrado_at = '2026-01-01 00:00:00' WHERE nic='6997885'")
        c.commit()

        purgados = db.purgar_papelera(c)

        assert purgados == ["6997885"]
        assert c.execute("SELECT COUNT(*) AS n FROM contratos WHERE nic='6997885'"
                         ).fetchone()["n"] == 0
        assert c.execute("SELECT COUNT(*) AS n FROM facturas WHERE nic='6997885'"
                         ).fetchone()["n"] == 0


def test_purgar_no_toca_lo_reciente(db_path):
    _guardar_contrato()
    with db.sesion() as c:
        db.eliminar_contrato(c, "6997885")
        assert db.purgar_papelera(c) == []                       # recien borrado
        assert c.execute("SELECT COUNT(*) AS n FROM contratos").fetchone()["n"] == 1


def test_restaurar_lo_devuelve_al_panel(db_path, monkeypatch):
    monkeypatch.setattr(dashboard, "requiere_admin", lambda request: None)
    _guardar_contrato()
    with db.sesion() as c:
        db.eliminar_contrato(c, "6997885")

    dashboard.restaurar_cliente("6997885", None)

    with db.sesion() as c:
        fila = c.execute("SELECT estado, activo, borrado_at FROM contratos WHERE nic='6997885'"
                         ).fetchone()
        assert [r["nic"] for r in db.listar_contratos(c)] == ["6997885"]
    assert fila["estado"] == "agregado" and fila["activo"] == 1 and fila["borrado_at"] is None


def test_volver_a_agregarlo_lo_saca_de_la_papelera(db_path):
    _guardar_contrato()
    with db.sesion() as c:
        db.eliminar_contrato(c, "6997885")
        assert len(db.contratos_papelera(c)) == 1

    _guardar_contrato()                                          # se agrega de nuevo

    with db.sesion() as c:
        assert db.contratos_papelera(c) == []
        fila = c.execute("SELECT estado, borrado_at FROM contratos WHERE nic='6997885'").fetchone()
    assert fila["estado"] == "agregado" and fila["borrado_at"] is None


def test_purgar_el_cliente_borra_tambien_sus_proyectos(db_path):
    _guardar_contrato()
    with db.sesion() as c:
        c.execute("INSERT INTO proyectos (nic, nombre) VALUES ('6997885', 'Techo')")
        c.commit()
        db.eliminar_contrato(c, "6997885")
        c.execute("UPDATE contratos SET borrado_at = '2026-01-01 00:00:00' WHERE nic='6997885'")
        c.commit()
        db.purgar_papelera(c)
        assert c.execute("SELECT COUNT(*) AS n FROM proyectos WHERE nic='6997885'"
                         ).fetchone()["n"] == 0


def test_una_fila_borrada_sin_fecha_no_se_purga_por_error(db_path):
    """Regresion: borrado_at NULL se tomaba como 'viejo' y se borraba de inmediato.

    Paso en real: un cliente eliminado en el panel (con una version que aun no
    escribia borrado_at) desaparecio para siempre en la primera purga.
    """
    _guardar_contrato()
    with db.sesion() as c:
        db.eliminar_contrato(c, "6997885")
        c.execute("UPDATE contratos SET borrado_at = NULL WHERE nic='6997885'")
        c.commit()

        assert db.purgar_papelera(c) == []                 # no se borra
        fila = c.execute("SELECT borrado_at FROM contratos WHERE nic='6997885'").fetchone()
        assert fila is not None and fila["borrado_at"]      # se sella la fecha
        assert c.execute("SELECT COUNT(*) AS n FROM facturas").fetchone()["n"] >= 0
