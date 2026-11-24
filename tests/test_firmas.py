"""Firmas y sellos: subir la imagen, elegir dónde va y que salga en el PDF.

Las rutas se prueban llamándolas directamente (como el resto del panel) para no
agregar httpx: la de subida es `async` y recibe el `UploadFile` de FastAPI.
"""
import asyncio
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
