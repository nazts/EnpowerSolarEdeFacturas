"""La ficha del cliente consulta el portal sola, sin esperar al botón.

El fallo original: `conviene_buscar` esperaba 2 horas desde el último evento
"Revisado NIC …" — y la corrida diaria de las 07:00 lo escribía — así que al
abrir una ficha el panel se quedaba quieto y había que pulsar «Buscar facturas».
Además el JS guardaba un candado en `sessionStorage`, que impedía repetir la
búsqueda durante toda la sesión del navegador.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app import auth, dashboard, db

NIC = "6997885"


def _contrato(db_path):
    with db.sesion() as c:
        db.upsert_contrato(c, NIC, razon_social="DE LA CRUZ TAVAREZ, JORGE RAMON",
                           dia_corte=16, proveedor="edenorte")


def _una_factura(db_path):
    with db.sesion() as c:
        db.guardar_factura(c, {"nic": NIC, "ref_pago": "6997885044", "corte": "2026-10-16",
                               "kwh": 35, "facturado": 249.28, "estado": "descargada"})


def _evento(hace_minutos: int, texto: str = None):
    """Simula la anotación que deja la corrida diaria."""
    from app import config
    with db.sesion() as c:
        cuando = (datetime.now() - timedelta(minutes=hace_minutos)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO eventos (nivel, mensaje, detalle, usuario, fecha)"
                  " VALUES (?,?,?,?,?)",
                  ("info", texto or f"Historico revisado NIC {NIC}", "", "", cuando))
        c.commit()


class Peticion:
    def __init__(self):
        # un operador conectado: solo ellos pueden consultar el portal
        self.state = SimpleNamespace(usuario=auth.Usuario(
            id=1, usuario="jefe", nombre="Jefe", rol="admin", activo=1))
        self.query_params = {}
        self.cookies = {}
        self.headers = {}
        self.url = SimpleNamespace(path=f"/clientes/{NIC}")


# ------------------------------------------------------------ la decisión
def test_sin_facturas_siempre_conviene_buscar(db_path):
    _contrato(db_path)
    with db.sesion() as c:
        assert dashboard.conviene_buscar(c, NIC) is True


def test_no_repite_si_acaba_de_buscar(db_path):
    _contrato(db_path)
    _una_factura(db_path)
    _evento(0)
    with db.sesion() as c:
        assert dashboard.conviene_buscar(c, NIC) is False


def test_la_corrida_de_las_7_no_bloquea_la_ficha(db_path):
    """El caso que reportó el usuario: se buscó al amanecer y la ficha se dormía."""
    _contrato(db_path)
    _una_factura(db_path)
    _evento(60)                       # corrida diaria, hace una hora
    with db.sesion() as c:
        assert dashboard.conviene_buscar(c, NIC) is True


# --------------------------------------------------------------- la ficha
def test_la_ficha_pide_la_busqueda_automatica(db_path):
    _contrato(db_path)
    _una_factura(db_path)
    _evento(60)

    respuesta = dashboard.cliente_detalle(Peticion(), NIC)

    assert respuesta.context["auto_buscar"] is True


def test_la_ficha_no_repite_tras_el_reload(db_path):
    """`?auto=1` es el que vuelve a pedir la página: ahí no se busca otra vez."""
    _contrato(db_path)
    _una_factura(db_path)
    _evento(60)
    peticion = Peticion()
    peticion.query_params = {"auto": "1"}

    respuesta = dashboard.cliente_detalle(peticion, NIC)

    assert respuesta.context["auto_buscar"] is False


def test_la_plantilla_muestra_el_aviso_de_busqueda():
    from pathlib import Path

    plantilla = (Path(__file__).resolve().parent.parent
                 / "app" / "templates" / "cliente_detalle.html").read_text(encoding="utf-8")

    assert 'id="autoBuscar"' in plantilla
    assert "fd.append('enviar', '0')" in plantilla      # consulta, no envía solo
    assert "sessionStorage" not in plantilla            # ya no hay candado de sesión
