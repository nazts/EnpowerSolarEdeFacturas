"""Firmas y sellos: subir la imagen, elegir dónde va y que salga en el PDF.

Las rutas se prueban llamándolas directamente (como el resto del panel) para no
agregar httpx: la de subida es `async` y recibe el `UploadFile` de FastAPI.
"""
import asyncio
from pathlib import Path
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from starlette.datastructures import Headers, UploadFile

from app import cotizacion_pdf, dashboard, db


def _png(ancho: int = 300, alto: int = 120) -> bytes:
    """Una firma falsa: un trazo oscuro con fondo transparente."""
    imagen = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    ImageDraw.Draw(imagen).line([(10, alto - 20), (ancho - 10, 20)], fill=(12, 30, 70, 255), width=6)
    buffer = BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()


class Peticion:
    def __init__(self, usuario=None, json_=None):
        self.state = SimpleNamespace(usuario=usuario)
        self.cookies = {}
        self.headers = {}
        self._json = json_ or {}

    async def json(self):
        return self._json


@pytest.fixture
def dueno(db_path, monkeypatch):
    """Un admin logueado y la carpeta de firmas apuntando a tmp_path."""
    from app import auth

    monkeypatch.setattr(dashboard, "CARPETA_FIRMAS", (db_path.parent / "firmas"))
    monkeypatch.setattr(cotizacion_pdf, "RAIZ_STATIC", db_path.parent)
    (db_path.parent / "firmas").mkdir(parents=True, exist_ok=True)
    with db.sesion() as conn:
        auth.crear_usuario(conn, "jefe", "ClaveSegura123", nombre="Jefe", rol="admin")
        return auth.autenticar(conn, "jefe", "ClaveSegura123")


def _subir(usuario, contenido: bytes, nombre="firma.png", tipo="image/png",
           etiqueta="Firma Ing. Luis", cargo="Coord. Presupuesto"):
    archivo = UploadFile(file=BytesIO(contenido), filename=nombre,
                         headers=Headers({"content-type": tipo}))
    return asyncio.run(dashboard.firmas_subir(
        request=Peticion(usuario=usuario), nombre=etiqueta, cargo=cargo, archivo=archivo))


def test_subir_una_firma_guarda_el_archivo_y_la_registra(dueno):
    respuesta = _subir(dueno, _png())
    assert respuesta.status_code == 303
    assert "mensaje=guardada" in respuesta.headers["location"]

    with db.sesion() as conn:
        firmas = db.lista_firmas(conn)
    assert len(firmas) == 1
    assert firmas[0]["nombre"] == "Firma Ing. Luis"
    assert firmas[0]["cargo"] == "Coord. Presupuesto"
    guardado = dashboard.CARPETA_FIRMAS / firmas[0]["archivo"]
    assert guardado.exists() and guardado.stat().st_size > 0


def test_rechaza_un_archivo_que_no_es_imagen(dueno):
    respuesta = _subir(dueno, b"esto no es una imagen", nombre="firma.png")
    assert "mensaje=tipo" in respuesta.headers["location"]
    with db.sesion() as conn:
        assert db.lista_firmas(conn) == []


def test_rechaza_un_tipo_no_permitido(dueno):
    respuesta = _subir(dueno, b"%PDF-1.4 ...", nombre="x.pdf", tipo="application/pdf")
    assert "mensaje=tipo" in respuesta.headers["location"]


def test_la_posicion_se_guarda_y_no_se_sale_de_la_hoja(dueno):
    _subir(dueno, _png())
    with db.sesion() as conn:
        firma_id = db.lista_firmas(conn)[0]["id"]

    r = asyncio.run(dashboard.firmas_posicion(
        request=Peticion(usuario=dueno, json_={"x": -500, "y": 99999, "ancho": 5000}),
        firma_id=firma_id))
    assert r.status_code == 200
    ancho_hoja, alto_hoja = dashboard._medidas_hoja()
    with db.sesion() as conn:
        firma = db.obtener_firma(conn, firma_id)
    assert 0 <= firma["x"] <= ancho_hoja
    assert 0 <= firma["y"] <= alto_hoja
    assert 40 <= firma["ancho"] <= ancho_hoja


def test_la_firma_elegida_sale_dibujada_en_el_pdf(dueno):
    _subir(dueno, _png())
    with db.sesion() as conn:
        firma_id = db.lista_firmas(conn)[0]["id"]
    asyncio.run(dashboard.firmas_posicion(
        request=Peticion(usuario=dueno, json_={"x": 70, "y": 150, "ancho": 180}),
        firma_id=firma_id))

    cot = {"numero": "000030", "fecha": "2026-11-24", "cliente_nombre": "CLIENTE X",
           "items": [{"descripcion": "Instalación", "cantidad": 1, "precio": 1000, "itbis": 18}],
           "comision_pct": 4.5, "firma": "Ing. Luis", "firma_id": firma_id}
    con_firma = cotizacion_pdf.generar(cot)
    sin_firma = cotizacion_pdf.generar({**cot, "firma_id": None})

    from pypdf import PdfReader

    imagenes_con = len(PdfReader(BytesIO(con_firma)).pages[0].images)
    imagenes_sin = len(PdfReader(BytesIO(sin_firma)).pages[0].images)
    assert imagenes_con == imagenes_sin + 1


def test_la_cotizacion_recuerda_la_firma_elegida(db_path):
    datos = cotizacion_pdf.calcular([{"descripcion": "X", "cantidad": 1, "precio": 100, "itbis": 18}], 4.5)
    with db.sesion() as conn:
        firma_id = db.guardar_firma(conn, {"nombre": "Sello", "archivo": "abc.png"})
        nuevo = db.guardar_cotizacion(conn, {**datos, "numero": "000031", "fecha": "2026-11-24",
                                             "cliente_nombre": "CLIENTE X",
                                             "firma_id": firma_id})
        cot = db.obtener_cotizacion(conn, nuevo)
    assert cot["firma_id"] == firma_id


def test_la_firma_no_se_sale_por_arriba_de_la_hoja(dueno):
    """La Y es la base de la imagen: se descuenta su alto."""
    _subir(dueno, _png(300, 120))
    with db.sesion() as conn:
        firma_id = db.lista_firmas(conn)[0]["id"]
    _, alto_hoja = dashboard._medidas_hoja()
    asyncio.run(dashboard.firmas_posicion(
        request=Peticion(usuario=dueno, json_={"x": 70, "y": 10 ** 6, "ancho": 200}),
        firma_id=firma_id))
    with db.sesion() as conn:
        firma = db.obtener_firma(conn, firma_id)
    alto_imagen = firma["ancho"] * 120 / 300       # la proporción del PNG de prueba
    assert firma["y"] + alto_imagen <= alto_hoja


def test_un_cuerpo_que_no_es_json_devuelve_400(dueno):
    _subir(dueno, _png())
    with db.sesion() as conn:
        firma_id = db.lista_firmas(conn)[0]["id"]

    class Roto(Peticion):
        async def json(self):
            raise ValueError("no es json")

    with pytest.raises(Exception) as error:
        asyncio.run(dashboard.firmas_posicion(request=Roto(usuario=dueno), firma_id=firma_id))
    assert getattr(error.value, "status_code", None) == 400


def test_solo_quien_opera_cambia_las_firmas(dueno):
    """Un asesor que sólo consulta no sube ni mueve firmas."""
    from app import auth

    with db.sesion() as conn:
        auth.crear_usuario(conn, "asesor", "ClaveSegura123", nombre="Asesor", rol="asesor",
                           modulos="cotizaciones")
        asesor = auth.autenticar(conn, "asesor", "ClaveSegura123")
    with pytest.raises(Exception) as error:
        _subir(asesor, _png())
    assert getattr(error.value, "status_code", None) == 403


# ------------------------------- firmar una cotización ya guardada
def _cotizacion_guardada(db_path):
    datos = cotizacion_pdf.calcular(
        [{"descripcion": "Instalación", "cantidad": 1, "precio": 100000, "itbis": 18}], 4.5)
    with db.sesion() as conn:
        return db.guardar_cotizacion(conn, {**datos, "numero": "000040", "fecha": "2026-11-24",
                                            "cliente_nombre": "CLIENTE FIRMA"})


def test_firmar_la_cotizacion_guarda_su_propia_posicion(dueno, db_path):
    _subir(dueno, _png())
    with db.sesion() as conn:
        firma_id = db.lista_firmas(conn)[0]["id"]
    cot_id = _cotizacion_guardada(db_path)

    respuesta = asyncio.run(dashboard.cotizaciones_firmar(
        request=Peticion(usuario=dueno, json_={"firma_id": firma_id, "x": 300, "y": 200,
                                               "ancho": 210}),
        cotizacion_id=cot_id))
    assert respuesta.status_code == 200
    with db.sesion() as conn:
        cot = db.obtener_cotizacion(conn, cot_id)
    assert cot["firma_id"] == firma_id
    assert round(cot["firma_x"]) == 300 and round(cot["firma_y"]) == 200
    assert round(cot["firma_ancho"]) == 210

    # el PDF sale con la firma
    from pypdf import PdfReader

    pdf = cotizacion_pdf.generar(cot)
    assert len(PdfReader(BytesIO(pdf)).pages[0].images) > 0


def test_quitar_la_firma_deja_la_cotizacion_sin_sello(dueno, db_path):
    _subir(dueno, _png())
    with db.sesion() as conn:
        firma_id = db.lista_firmas(conn)[0]["id"]
    cot_id = _cotizacion_guardada(db_path)
    asyncio.run(dashboard.cotizaciones_firmar(
        request=Peticion(usuario=dueno, json_={"firma_id": firma_id, "x": 70, "y": 130,
                                               "ancho": 160}), cotizacion_id=cot_id))
    asyncio.run(dashboard.cotizaciones_firmar(
        request=Peticion(usuario=dueno, json_={"firma_id": None}), cotizacion_id=cot_id))
    with db.sesion() as conn:
        cot = db.obtener_cotizacion(conn, cot_id)
    assert cot["firma_id"] is None and cot["firma_x"] is None


def test_la_hoja_de_la_cotizacion_se_dibuja_para_firmar(dueno, db_path):
    cot_id = _cotizacion_guardada(db_path)
    respuesta = dashboard.cotizaciones_hoja(cot_id, request=Peticion(usuario=dueno))
    assert respuesta.media_type == "image/png"
    assert respuesta.body.startswith(b"\x89PNG")


def test_la_pantalla_de_firmar_se_arma_con_y_sin_firmas(dueno, db_path, monkeypatch):
    from starlette.requests import Request

    cot_id = _cotizacion_guardada(db_path)
    ancho, alto = dashboard._medidas_hoja()

    def pedir():
        scope = {"type": "http", "method": "GET", "path": f"/cotizaciones/{cot_id}/firmar",
                 "headers": [], "app": dashboard.app, "router": dashboard.app.router,
                 "root_path": "", "scheme": "http", "server": ("127.0.0.1", 8000),
                 "query_string": b""}
        r = Request(scope)
        r.state.usuario = dueno
        return r

    # sin firmas guardadas: ofrece agregar una
    html = dashboard.templates.env.get_template("cotizacion_firmar.html").render(
        request=pedir(), activo="cotizaciones", cotizacion={"id": cot_id, "numero": "000040",
                                                           "cliente_nombre": "X", "fecha": "2026-11-24",
                                                           "total": 118000, "firma_id": None,
                                                           "firma_x": None, "firma_y": None,
                                                           "firma_ancho": None},
        firmas=[], ancho_hoja=ancho, alto_hoja=alto, mensaje="")
    assert "Agregar una firma" in html

    # con firmas: aparecen las opciones y el botón de guardar
    _subir(dueno, _png())
    with db.sesion() as conn:
        firmas = db.lista_firmas(conn)
    html = dashboard.templates.env.get_template("cotizacion_firmar.html").render(
        request=pedir(), activo="cotizaciones", cotizacion={"id": cot_id, "numero": "000040",
                                                           "cliente_nombre": "X", "fecha": "2026-11-24",
                                                           "total": 118000, "firma_id": None,
                                                           "firma_x": None, "firma_y": None,
                                                           "firma_ancho": None},
        firmas=firmas, ancho_hoja=ancho, alto_hoja=alto, mensaje="")
    assert "Guardar firma" in html and "editor_firma.js" in html


def test_la_hoja_se_conviete_a_png_con_el_conversor_comun():
    """El endpoint no depende de pypdfium2: hay respaldo con pdftoppm."""
    from app import config, pdfdata

    datos = Path(config.PLANTILLA_COTIZACION).read_bytes()
    png = pdfdata.pdf_a_png(datos, escala=1.0, pagina=1)
    assert png.startswith(b"\x89PNG")
    assert len(png) > 5000
