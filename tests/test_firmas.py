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
        request=Peticion(usuario=dueno, json_={"firma_id": firma_id, "x": 300, "y": 120,
                                               "ancho": 210}),
        cotizacion_id=cot_id))
    assert respuesta.status_code == 200
    with db.sesion() as conn:
        cot = db.obtener_cotizacion(conn, cot_id)
    assert cot["firma_id"] == firma_id
    assert round(cot["firma_x"]) == 300 and round(cot["firma_y"]) == 120
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


def test_la_firma_se_queda_exactamente_donde_se_puso(dueno, db_path):
    """El punto elegido se respeta: sólo se recorta si se sale de la hoja."""
    _subir(dueno, _png())
    with db.sesion() as conn:
        firma_id = db.lista_firmas(conn)[0]["id"]
    cot_id = _cotizacion_guardada(db_path)
    asyncio.run(dashboard.cotizaciones_firmar(
        request=Peticion(usuario=dueno, json_={"firma_id": firma_id, "x": 100, "y": 600,
                                               "ancho": 200}), cotizacion_id=cot_id))
    with db.sesion() as conn:
        cot = db.obtener_cotizacion(conn, cot_id)
        firma = db.obtener_firma(conn, firma_id)
    assert round(cot["firma_y"]) == 600 and round(cot["firma_x"]) == 100
    assert round(cot["firma_ancho"]) == 200


# ------------------------------- imagen: fondo, recorte y posición exacta
def test_la_firma_escaneada_pierde_el_fondo_blanco():
    """Un JPEG sobre papel blanco taparía los totales: se le quita el fondo."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    from app.firmas_imagen import preparar_firma

    papel = Image.new("RGB", (400, 150), (255, 255, 255))
    ImageDraw.Draw(papel).line([(30, 120), (370, 30)], fill=(15, 30, 90), width=6)
    buffer = BytesIO()
    papel.save(buffer, format="JPEG", quality=92)

    png = preparar_firma(buffer.getvalue())
    imagen = Image.open(BytesIO(png))
    assert imagen.format == "PNG"
    alfa = imagen.convert("RGBA").getchannel("A")
    assert alfa.getextrema()[0] == 0           # hay zonas transparentes
    # el tamaño NO cambia: así la firma se queda donde se ubicó
    assert imagen.size == (400, 150)


def test_un_sello_con_blanco_adentro_no_se_perfora():
    """El blanco del centro es parte del diseño: sólo se limpia el fondo de afuera."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    from app.firmas_imagen import preparar_firma

    sello = Image.new("RGB", (300, 300), (255, 255, 255))
    dibujo = ImageDraw.Draw(sello)
    dibujo.ellipse([30, 30, 270, 270], outline=(30, 70, 45), width=10)
    dibujo.ellipse([110, 110, 190, 190], fill=(255, 255, 255))
    buffer = BytesIO()
    sello.save(buffer, format="PNG")

    imagen = Image.open(BytesIO(preparar_firma(buffer.getvalue()))).convert("RGBA")
    assert imagen.getpixel((1, 1))[3] == 0                      # esquina transparente
    assert imagen.getpixel((imagen.width // 2, imagen.height // 2))[3] == 255


def test_la_firma_cae_exactamente_donde_se_ubico(tmp_path, monkeypatch):
    """(x, y) es la esquina inferior izquierda: antes se dibujaba centrada."""
    from io import BytesIO

    from pypdf import PdfReader

    from app import cotizacion_pdf

    (tmp_path / "firmas").mkdir()
    (tmp_path / "firmas" / "prueba_posicion.png").write_bytes(_png(400, 100))
    monkeypatch.setattr(cotizacion_pdf, "RAIZ_STATIC", tmp_path)

    cot = {"numero": "000050", "fecha": "2026-11-24", "cliente_nombre": "X",
           "items": [{"descripcion": "A", "cantidad": 1, "precio": 1000, "itbis": 18}],
           "comision_pct": 4.5, "notas": "", "firma_id": None,
           "firma_imagen": {"archivo": "prueba_posicion.png", "nombre": "t", "cargo": "",
                            "x": 250.0, "y": 150.0, "ancho": 100.0}}
    pdf = PdfReader(BytesIO(cotizacion_pdf.generar(cot)))

    # la imagen se dibuja con su esquina inferior izquierda en (x, y)
    contenidos = b"".join(pagina.get_contents().get_data() for pagina in pdf.pages)
    assert b"250" in contenidos or b"250.0" in contenidos
    # y el PDF abre bien
    assert pdf.pages[0].mediabox.width > 500


def test_la_linea_se_apoya_donde_termina_la_tinta(tmp_path):
    """Si el PNG trae aire abajo, la línea no queda flotando por encima."""
    from PIL import Image, ImageDraw

    from app.cotizacion_pdf import _borde_inferior_tinta

    ruta = tmp_path / "con-aire.png"
    imagen = Image.new("RGBA", (400, 200), (0, 0, 0, 0))       # 200 px de alto
    ImageDraw.Draw(imagen).rectangle([20, 20, 380, 120], fill=(10, 30, 90, 255))
    imagen.save(ruta)

    borde = _borde_inferior_tinta(ruta)      # la tinta termina a 121 px de 200
    assert 0.55 < borde < 0.65
    assert _borde_inferior_tinta(tmp_path / "no-existe.png") == 1.0


def test_subir_un_png_no_cambia_su_tamano(dueno):
    """Lo que se sube mantiene ancho y alto: la firma no se corre al guardarla."""
    from io import BytesIO

    from PIL import Image

    datos = _png(300, 120)
    _subir(dueno, datos)
    with db.sesion() as conn:
        firma = db.lista_firmas(conn)[0]
    guardado = dashboard.CARPETA_FIRMAS / firma["archivo"]
    assert guardado.suffix == ".png"
    assert Image.open(guardado).size == Image.open(BytesIO(datos)).size


def test_el_editor_no_confunde_sin_posicion_con_cero():
    """`Number('')` da 0: sin este cuidado la firma arrancaba en la esquina (0,0)."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "app" / "static" / "js"
          / "editor_firma.js").read_text(encoding="utf-8")
    assert "function numero(valor, porDefecto)" in js
    assert "numero(opciones.x, 60)" in js and "numero(opciones.y, LINEA_FIRMA)" in js


# ------------------------------- visibilidad del guardado
def test_guardar_la_posicion_deja_un_evento(dueno):
    """Antes no quedaba rastro: no se podía saber si el POST había llegado."""
    _subir(dueno, _png())
    with db.sesion() as conn:
        firma_id = db.lista_firmas(conn)[0]["id"]
    asyncio.run(dashboard.firmas_posicion(
        request=Peticion(usuario=dueno, json_={"x": 200, "y": 150, "ancho": 150}),
        firma_id=firma_id))
    with db.sesion() as conn:
        fila = conn.execute(
            "SELECT mensaje, detalle FROM eventos WHERE mensaje LIKE 'Firma ubicada%' "
            "ORDER BY id DESC LIMIT 1").fetchone()
    assert fila is not None
    assert "x=200" in fila["detalle"] and "ancho=150" in fila["detalle"]


def test_un_usuario_sin_permiso_deja_el_rechazo_en_eventos(dueno):
    from app import auth

    with db.sesion() as conn:
        auth.crear_usuario(conn, "asesor", "ClaveSegura123", nombre="Asesor", rol="asesor",
                           modulos="cotizaciones")
        asesor = auth.autenticar(conn, "asesor", "ClaveSegura123")
    with pytest.raises(Exception):
        _subir(asesor, _png())
    with db.sesion() as conn:
        fila = conn.execute(
            "SELECT mensaje, detalle FROM eventos WHERE mensaje LIKE 'Sin permiso%' "
            "ORDER BY id DESC LIMIT 1").fetchone()
    assert fila is not None and "asesor" in fila["detalle"]


def test_el_listado_muestra_lo_que_quedo_guardado(dueno):
    """El aviso sale con los valores de la base, no con los de la pantalla."""
    from starlette.requests import Request

    _subir(dueno, _png())
    with db.sesion() as conn:
        firma_id = db.lista_firmas(conn)[0]["id"]
    asyncio.run(dashboard.firmas_posicion(
        request=Peticion(usuario=dueno, json_={"x": 111, "y": 99, "ancho": 120}),
        firma_id=firma_id))

    def pedir():
        scope = {"type": "http", "method": "GET", "path": "/firmas", "headers": [],
                 "app": dashboard.app, "router": dashboard.app.router, "root_path": "",
                 "scheme": "http", "server": ("127.0.0.1", 8000), "query_string": b""}
        r = Request(scope)
        r.state.usuario = dueno
        return r

    with db.sesion() as conn:
        firmas = dashboard._con_borde(db.lista_firmas(conn))
        ubicada = db.obtener_firma(conn, firma_id)
    ancho, alto = dashboard._medidas_hoja()
    html = dashboard.templates.env.get_template("firmas.html").render(
        request=pedir(), activo="firmas", firmas=firmas, ancho_hoja=ancho, alto_hoja=alto,
        mensaje="ubicada", nueva="", ubicada=ubicada)
    assert "Posición guardada en la base" in html
    assert "x 111" in html and "y 99" in html


def test_si_falta_el_archivo_no_se_pierden_las_coordenadas(tmp_path, monkeypatch):
    """El bug: las coordenadas estaban dentro del `if archivo.exists()`.

    Si el PNG no estaba en el servidor se dibujaba el del .env en el sitio fijo.
    """
    from io import BytesIO

    from pypdf import PdfReader

    from app import config, cotizacion_pdf

    # hay un PNG por defecto configurado…
    por_defecto = tmp_path / "firma-env.png"
    por_defecto.write_bytes(_png(300, 120))
    monkeypatch.setattr(config, "FIRMA_COTIZACION", por_defecto)
    monkeypatch.setattr(cotizacion_pdf, "RAIZ_STATIC", tmp_path)   # sin carpeta firmas

    cot = {"numero": "000070", "fecha": "2026-11-26", "cliente_nombre": "X",
           "items": [{"descripcion": "A", "cantidad": 1, "precio": 100, "itbis": 18}],
           "comision_pct": 4.5, "notas": "", "firma": "Ing. Luis",
           "firma_imagen": {"archivo": "no-esta.png", "nombre": "Ing. Luis", "cargo": "",
                            "x": 300.0, "y": 200.0, "ancho": 180.0}}
    # referencia: sin firma elegida se dibuja la del .env (una imagen más)
    sin_firma = PdfReader(BytesIO(cotizacion_pdf.generar({**cot, "firma_imagen": None})))
    con_firma_rota = PdfReader(BytesIO(cotizacion_pdf.generar(cot)))
    assert len(con_firma_rota.pages[0].images) == len(sin_firma.pages[0].images) - 1


def test_avisa_en_actividad_cuando_falta_la_imagen(tmp_path, monkeypatch, db_path):
    from app import cotizacion_pdf

    monkeypatch.setattr(cotizacion_pdf, "RAIZ_STATIC", tmp_path)
    cotizacion_pdf._avisa_si_falta_el_archivo({"archivo": "no-esta.png", "nombre": "Firma X"})
    with db.sesion() as c:
        fila = c.execute("SELECT mensaje, detalle FROM eventos WHERE mensaje LIKE '%no está en el servidor%' "
                         "ORDER BY id DESC LIMIT 1").fetchone()
    assert fila is not None and "no-esta.png" in fila["detalle"]
