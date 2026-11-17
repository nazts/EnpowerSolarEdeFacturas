"""Cotizaciones: cuentas, PDF con el formato de la empresa y módulos por usuario."""
import pytest
from fastapi import HTTPException

from app import auth, cotizacion_pdf, dashboard, db


# ------------------------------------------------------------------ cálculo
def test_calcula_subtotal_itbis_y_total():
    items = [
        {"descripcion": "Panel 590W", "cantidad": 10, "precio": 1000, "itbis": 18},
        {"descripcion": "Batería", "cantidad": 2, "precio": 5000, "itbis": 0},
    ]
    datos = cotizacion_pdf.calcular(items, 4.5)

    assert datos["subtotal"] == 20000.0
    assert datos["itbis"] == 1800.0            # solo el primero lleva itbis
    assert datos["total"] == 21800.0
    assert datos["comision"] == 981.0          # 4.5% del total
    assert datos["items"][0]["importe"] == 11800.0


def test_itbis_por_defecto_es_18_por_ciento():
    datos = cotizacion_pdf.calcular([{"descripcion": "x", "cantidad": 1, "precio": 100}], 0)
    assert datos["itbis"] == 18.0 and datos["total"] == 118.0
    assert datos["comision"] == 0.0


def test_cantidades_y_precios_en_texto_no_revientan():
    datos = cotizacion_pdf.calcular(
        [{"descripcion": "x", "cantidad": "", "precio": "abc", "itbis": ""}], 4.5)
    assert datos["total"] == 0.0 and datos["items"][0]["cantidad"] == 0.0


# ---------------------------------------------------------------------- PDF
def _cotizacion():
    return {
        "numero": "000001", "fecha": "2026-11-17",
        "cliente_nombre": "CLIENTE DE PRUEBA SRL", "cliente_rnc": "1-31-12345-6",
        "cliente_direccion": "Santiago", "moneda": "Pesos Dominicanos",
        "items": [{"descripcion": "Panel solar 590W", "cantidad": 4, "precio": 12500,
                   "itbis": 18}],
        "comision_pct": 4.5, "notas": "Instalación incluida.",
    }


def test_el_pdf_se_genera_y_es_un_pdf():
    pdf = cotizacion_pdf.generar(_cotizacion())
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_el_pdf_no_lleva_la_firma():
    """La plantilla original trae la firma del coordinador: aquí no va."""
    pdf = cotizacion_pdf.generar(_cotizacion())
    texto = pdf.decode("latin-1", "ignore")
    for palabra in ("Emmanuel", "Coordinador", "Coord.", "DEP. PRESUPUESTO", "Firma"):
        assert palabra.lower() not in texto.lower()


def test_el_pdf_aguanta_nombres_con_acentos():
    cot = _cotizacion()
    cot["cliente_nombre"] = "JOSÉ RAMÓN NÚÑEZ — ÑANDÚ"
    cot["notas"] = "Cotización válida. Dirección: Av. Circunvalación."
    pdf = cotizacion_pdf.generar(cot)
    assert pdf.startswith(b"%PDF")


# ------------------------------------------------------ guardar y recuperar
def test_guardar_y_leer_la_cotizacion(db_path):
    datos = cotizacion_pdf.calcular(_cotizacion()["items"], 4.5)
    with db.sesion() as c:
        numero = db.siguiente_numero_cotizacion(c)
        nuevo_id = db.guardar_cotizacion(c, {
            "numero": numero, "fecha": "2026-11-17", "cliente_nombre": "CLIENTE X",
            "cliente_rnc": "1-1", "cliente_direccion": "Santiago",
            "items": datos["items"], "subtotal": datos["subtotal"],
            "itbis": datos["itbis"], "comision_pct": 4.5, "comision": datos["comision"],
            "total": datos["total"], "creado_por": "presupuesto1",
        })
        cot = db.obtener_cotizacion(c, nuevo_id)
        lista = db.lista_cotizaciones(c)

    assert cot is not None
    assert cot["numero"] == "000001"
    assert cot["total"] == datos["total"]
    assert cot["items"][0]["descripcion"] == "Panel solar 590W"
    assert [x["id"] for x in lista] == [nuevo_id]


def test_el_numero_sigue_desde_la_13(db_path):
    """Ya habia 12 cotizaciones hechas fuera del panel: la proxima es la 13."""
    datos = cotizacion_pdf.calcular(_cotizacion()["items"], 4.5)
    with db.sesion() as c:
        primero = db.siguiente_numero_cotizacion(c)
        db.guardar_cotizacion(c, {**datos, "numero": primero, "fecha": "2026-11-17"})
        segundo = db.siguiente_numero_cotizacion(c)
    assert primero == "000013" and segundo == "000014"


# -------------------------------------------------------- módulos por usuario
def test_admin_ve_todos_los_modulos():
    admin = auth.Usuario(id=1, usuario="a", nombre="A", rol="admin", activo=1)
    for modulo, _ in auth.MODULOS:
        assert admin.ve(modulo)


def test_sin_modulos_elegidos_se_usan_los_del_rol():
    asesor = auth.Usuario(id=2, usuario="b", nombre="B", rol="asesor", activo=1)
    assert asesor.ve("clientes") and not asesor.ve("cotizaciones")

    soporte = auth.Usuario(id=3, usuario="c", nombre="C", rol="soporte", activo=1)
    assert soporte.ve("cotizaciones") and not soporte.ve("usuarios")


def test_los_modulos_elegidos_mandan_sobre_el_rol():
    u = auth.Usuario(id=4, usuario="p", nombre="P", rol="asesor", activo=1,
                     modulos="cotizaciones")
    assert u.ve("cotizaciones") and not u.ve("clientes") and not u.ve("panel")


def test_requiere_modulo_corta_el_acceso(monkeypatch):
    class Peticion:
        state = type("S", (), {"usuario": auth.Usuario(
            id=5, usuario="p", nombre="P", rol="asesor", activo=1, modulos="cotizaciones")})()

    dashboard.requiere_modulo(Peticion(), "cotizaciones")          # pasa
    with pytest.raises(HTTPException) as excinfo:
        dashboard.requiere_modulo(Peticion(), "usuarios")
    assert excinfo.value.status_code == 403


def test_inicio_para_lleva_al_primer_modulo():
    u = auth.Usuario(id=6, usuario="p", nombre="P", rol="asesor", activo=1,
                     modulos="cotizaciones")
    assert dashboard.inicio_para(u) == "/cotizaciones"
    jefe = auth.Usuario(id=7, usuario="a", nombre="A", rol="admin", activo=1)
    assert dashboard.inicio_para(jefe) == "/"
