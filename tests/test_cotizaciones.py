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


def test_el_pdf_no_lleva_la_firma_impresa():
    """Queda la línea para firmar, pero sin nombres impresos en el código."""
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
    assert cot["numero"] == numero == "000013"
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


# ------------------------------------ PDF sobre la plantilla de la empresa
def _paginas(pdf: bytes) -> list[str]:
    from io import BytesIO

    from pypdf import PdfReader

    return [p.extract_text() or "" for p in PdfReader(BytesIO(pdf)).pages]


def test_el_pdf_usa_la_plantilla_de_la_empresa():
    """El fondo es PIE DE PÁGINA.pdf: logo, marca de agua y pie del diseñador."""
    pdf = cotizacion_pdf.generar(_cotizacion())
    paginas = _paginas(pdf)
    assert len(paginas) == 1
    # datos que sólo están en la plantilla (pie de página)
    assert "33-77201-9" in paginas[0]
    assert "Santiago" in paginas[0]


def test_el_pdf_dibuja_los_datos_de_la_cotizacion():
    pdf = cotizacion_pdf.generar(_cotizacion())
    texto = _paginas(pdf)[0]
    assert "000001" in texto
    assert "CLIENTE DE PRUEBA SRL" in texto
    assert "Panel solar 590W" in texto
    assert "Total DOP" in texto


def test_una_cotizacion_corta_entra_en_una_hoja():
    assert len(_paginas(cotizacion_pdf.generar(_cotizacion()))) == 1


def test_una_cotizacion_larga_pasa_a_la_segunda_hoja():
    """Si los renglones no caben, la tabla sigue en otra hoja con la plantilla."""
    cot = _cotizacion()
    cot["items"] = [{"descripcion": f"Panel número {i}", "cantidad": 1,
                     "precio": 1000 + i, "itbis": 18} for i in range(40)]
    pdf = cotizacion_pdf.generar(cot)

    paginas = _paginas(pdf)
    assert len(paginas) >= 2
    # el encabezado de la tabla se repite en la continuación
    assert "Descripción" in paginas[0] and "Descripción" in paginas[1]
    # el cierre (totales y firma) va una sola vez, en la última hoja
    assert sum("Total DOP" in p for p in paginas) == 1


def test_la_firma_png_es_opcional(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    from app import config

    # sin PNG: se genera igual (queda la línea para firmar a mano)
    monkeypatch.setattr(config, "FIRMA_COTIZACION", tmp_path / "no-existe.png")
    pdf = cotizacion_pdf.generar(_cotizacion())
    assert pdf.startswith(b"%PDF")

    # con PNG: se incrusta la imagen
    firma = tmp_path / "firma.png"
    imagen = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    ImageDraw.Draw(imagen).line([(10, 40), (190, 20)], fill=(10, 30, 70, 255), width=4)
    imagen.save(firma)
    monkeypatch.setattr(config, "FIRMA_COTIZACION", firma)
    con_firma = cotizacion_pdf.generar(_cotizacion())
    assert len(con_firma) > len(pdf)          # lleva la imagen adentro


# ------------------------------- una cotización no se guarda dos veces
def test_el_token_evita_guardar_la_misma_cotizacion_dos_veces(db_path):
    """Doble clic o volver atrás: el segundo envío no crea otra fila."""
    from app import cotizacion_pdf, db

    datos = cotizacion_pdf.calcular(_cotizacion()["items"], 4.5)
    with db.sesion() as c:
        token = "abc123"
        primero = db.guardar_cotizacion(c, {**datos, "numero": "000013",
                                           "fecha": "2026-11-17", "token": token,
                                           "cliente_nombre": "CLIENTE X"})
        previa = db.cotizacion_por_token(c, token)
        assert previa is not None and previa["id"] == primero

        # el índice único impide el duplicado aunque se llame dos veces
        import pytest as _pytest
        with _pytest.raises(Exception):
            db.guardar_cotizacion(c, {**datos, "numero": "000014",
                                      "fecha": "2026-11-17", "token": token})
        total = c.execute("SELECT COUNT(*) AS n FROM cotizaciones").fetchone()["n"]
    assert total == 1


def test_sin_token_se_pueden_guardar_varias(db_path):
    """Dos cotizaciones distintas (sin token) siguen siendo válidas."""
    from app import cotizacion_pdf, db

    datos = cotizacion_pdf.calcular(_cotizacion()["items"], 4.5)
    with db.sesion() as c:
        db.guardar_cotizacion(c, {**datos, "numero": "000013", "fecha": "2026-11-17"})
        db.guardar_cotizacion(c, {**datos, "numero": "000014", "fecha": "2026-11-18"})
        total = c.execute("SELECT COUNT(*) AS n FROM cotizaciones").fetchone()["n"]
    assert total == 2


def test_la_firma_se_guarda_y_sale_impresa_en_el_pdf(db_path):
    from io import BytesIO

    from pypdf import PdfReader

    from app import cotizacion_pdf, db

    datos = cotizacion_pdf.calcular(_cotizacion()["items"], 4.5)
    with db.sesion() as c:
        nuevo_id = db.guardar_cotizacion(c, {**datos, "numero": "000013",
                                             "fecha": "2026-11-17",
                                             "cliente_nombre": "CLIENTE X",
                                             "firma": "Ing. Luis"})
        cot = db.obtener_cotizacion(c, nuevo_id)

    assert cot["firma"] == "Ing. Luis"
    texto = PdfReader(BytesIO(cotizacion_pdf.generar(cot))).pages[0].extract_text()
    assert "Ing. Luis" in texto


def test_la_migracion_agrega_firma_y_token_a_cotizaciones_existentes(tmp_path, monkeypatch):
    """Una base vieja (sin firma ni token) se actualiza al arrancar."""
    import sqlite3

    from app import config, db

    ruta = tmp_path / "vieja.db"
    conn = sqlite3.connect(ruta)
    conn.execute("""CREATE TABLE cotizaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT, numero TEXT NOT NULL, fecha TEXT NOT NULL,
        cliente_nombre TEXT NOT NULL DEFAULT '', cliente_rnc TEXT NOT NULL DEFAULT '',
        cliente_direccion TEXT NOT NULL DEFAULT '', moneda TEXT NOT NULL DEFAULT '',
        items TEXT NOT NULL DEFAULT '[]', subtotal REAL DEFAULT 0, itbis REAL DEFAULT 0,
        comision_pct REAL DEFAULT 4.5, comision REAL DEFAULT 0, total REAL DEFAULT 0,
        notas TEXT NOT NULL DEFAULT '', creado_por TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '')""")
    conn.execute("INSERT INTO cotizaciones (numero, fecha) VALUES ('000007', '2026-01-01')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(config, "DB_PATH", ruta)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    with db.sesion(db_path=ruta) as c:
        columnas = {f[1] for f in c.execute("PRAGMA table_info(cotizaciones)")}
    assert {"firma", "token"} <= columnas
