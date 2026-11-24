"""Tablero de inicio (diseño «Flow») y el tablero clásico que se conserva."""
from types import SimpleNamespace

import pytest

from app import auth, dashboard, db


class Peticion:
    def __init__(self, usuario=None):
        self.state = SimpleNamespace(usuario=usuario)
        self.query_params = {}
        self.cookies = {}
        self.headers = {}
        self.url = SimpleNamespace(path="/")


def _datos_minimos(db_path):
    with db.sesion() as c:
        db.upsert_contrato(c, "6997885", razon_social="DE LA CRUZ TAVAREZ, JORGE RAMON",
                           dia_corte=16, proveedor="edenorte")
        db.guardar_factura(c, {"nic": "6997885", "ref_pago": "6997885044",
                               "corte": "2026-11-16", "kwh": 38, "facturado": 267.19,
                               "estado": "descargada"})


def test_el_tablero_entrega_los_agregados_que_pinta(db_path):
    _datos_minimos(db_path)
    resp = dashboard.inicio(Peticion(auth.Usuario(id=1, usuario="a", nombre="A",
                                                  rol="admin", activo=1)))
    ctx = resp.context

    assert ctx["resumen"]["clientes"]["total"] == 1
    assert ctx["resumen"]["facturas"]["total"] == 1
    assert isinstance(ctx["resumen"]["top_clientes"], list)
    assert isinstance(ctx["resumen"]["por_proveedor"], list)
    assert ctx["clientes"] and ctx["facturas"]


def test_el_tablero_pinta_las_tres_vistas(db_path):
    _datos_minimos(db_path)
    ctx = dict(dashboard.inicio(Peticion(auth.Usuario(id=1, usuario="a", nombre="A",
                                                     rol="admin", activo=1))).context)
    html = dashboard.templates.env.get_template("index.html").render(**ctx)

    for trozo in ('id="vista-resumen"', 'id="vista-clientes"', 'id="vista-actividad"',
                  'id="tablaClientes"', 'id="btnCsv"', "Facturación por mes",
                  "Top clientes por monto", "Distribuidora"):
        assert trozo in html, trozo


def test_el_js_del_tablero_apunta_a_los_endpoints_reales():
    from pathlib import Path

    plantilla = (Path(__file__).resolve().parent.parent
                 / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "/api/eventos?desde=" in plantilla        # log en vivo
    assert "clientes-enpower.csv" in plantilla       # exportación


def test_el_tablero_clasico_sigue_existiendo(db_path):
    _datos_minimos(db_path)
    resp = dashboard.panel_clasico(Peticion(auth.Usuario(id=1, usuario="a", nombre="A",
                                                         rol="admin", activo=1)))
    assert resp.template.name == "panel_clasico.html"


def test_el_tablero_clasico_pide_sesion(db_path):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        dashboard.panel_clasico(Peticion(None))
    assert excinfo.value.status_code == 401
