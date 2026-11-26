"""Cotizaciones: cuentas, PDF con el formato de la empresa y módulos por usuario."""
import pytest
from fastapi import HTTPException

from app import auth, cotizacion_pdf, dashboard, db


# ------------------------------------------------------------------ cálculo
def test_calcula_subtotal_itbis_y_total():
    # la comisión se aplica POR UNIDAD: el precio mostrado ya la incluye y el
    # sub total la refleja; el itbis se calcula sobre ese sub total.
    items = [
        {"descripcion": "Panel 590W", "cantidad": 10, "precio": 1000, "itbis": 18},
        {"descripcion": "Batería", "cantidad": 2, "precio": 5000, "itbis": 18},
    ]
    datos = cotizacion_pdf.calcular(items, 4.5)

    assert datos["comision"] == 900.0          # 4.5% del bruto (dato de control)
    assert datos["subtotal"] == 20900.0        # 20000 × 1.045 (la comisión va dentro)
    assert datos["itbis"] == 3762.0            # 18% sobre 20900
    assert datos["total"] == 24662.0
    assert datos["items"][0]["precio"] == 1045.0   # 1000 × 1.045
    # la tabla suma exactamente el total (todo cuadra)
    assert abs(datos["items"][0]["importe"] + datos["items"][1]["importe"]
               - datos["total"]) < 0.02


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

    from app import cotizacion_pdf

    # sin firma: se genera igual (queda la línea para firmar a mano)
    pdf = cotizacion_pdf.generar(_cotizacion())
    assert pdf.startswith(b"%PDF")

    # con firma elegida y posición guardada: se incrusta la imagen
    (tmp_path / "firmas").mkdir(exist_ok=True)
    firma = tmp_path / "firmas" / "firma.png"
    imagen = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    ImageDraw.Draw(imagen).line([(10, 40), (190, 20)], fill=(10, 30, 70, 255), width=4)
    imagen.save(firma)
    monkeypatch.setattr(cotizacion_pdf, "RAIZ_STATIC", tmp_path)
    cot = _cotizacion()
    cot["firma_imagen"] = {"archivo": "firma.png", "nombre": "Ing. Luis", "cargo": "",
                           "x": 300, "y": 200, "ancho": 160}
    con_firma = cotizacion_pdf.generar(cot)
    assert len(con_firma) > len(pdf)          # lleva la imagen adentro


# ------------------------------- una cotización no se guarda dos veces
def test_la_cotizacion_tiene_una_nota_interna_editable(db_path):
    """Nota para el sistema: se guarda al crear y se puede cambiar después."""
    from app import cotizacion_pdf, db

    datos = cotizacion_pdf.calcular(_cotizacion()["items"], 4.5)
    with db.sesion() as c:
        id_cot = db.guardar_cotizacion(c, {**datos, "numero": "000050",
                                           "fecha": "2026-11-26", "token": "tk-nota",
                                           "cliente_nombre": "CLIENTE X",
                                           "notas": "para el cliente",
                                           "nota_interna": "se le ajustó el precio"})
        cot = db.obtener_cotizacion(c, id_cot)
        assert cot["nota_interna"] == "se le ajustó el precio"

        db.anotar_cotizacion(c, id_cot, "se cambió la comisión a 3%")
        cot = db.obtener_cotizacion(c, id_cot)
        assert cot["nota_interna"] == "se cambió la comisión a 3%"


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


# ------------------------------- descripciones largas en una sola casilla
DESCRIPCION_LARGA = ("Tercer Nivel:\n"
                     "- Instalación de salidas para tomacorrientes en habitaciones y salidas para TV.\n"
                     "- Instalación de salidas para tomacorrientes y distribución de luces en baños.\n"
                     "- Previsión de líneas eléctricas para interruptores inteligentes.\n"
                     "- Instalación de salidas para lámparas.\n"
                     "- Conexiones Three-Way entre tercer y segundo nivel.\n"
                     "- Instalación de salidas eléctricas y rieles en closets.")


def _cotizacion_descriptiva():
    cot = _cotizacion()
    cot["items"] = [{"descripcion": DESCRIPCION_LARGA, "cantidad": 1,
                     "precio": 135000, "itbis": 0}]
    return cot


def test_la_descripcion_larga_no_se_desborda_de_la_casilla():
    """Envolvía sin sangría y se veía apretada: ahora cada viñeta va aparte."""
    texto = _paginas(cotizacion_pdf.generar(_cotizacion_descriptiva()))[0]
    assert "Tercer Nivel:" in texto
    # la última viñeta sigue visible (no se sale de la hoja ni se corta)
    assert "rieles en closets" in texto
    # el título se separa de las viñetas y ninguna línea pierde el texto
    assert "Previsión de líneas eléctricas" in texto


def test_la_descripcion_se_parte_con_el_encabezado_repetido():
    """Un concepto con muchas viñetas ocupa más de una hoja sin perder texto."""
    cot = _cotizacion()
    cot["items"] = [{"descripcion": DESCRIPCION_LARGA, "cantidad": 1,
                     "precio": 50000 + i, "itbis": 0} for i in range(6)]
    paginas = _paginas(cotizacion_pdf.generar(cot))
    assert len(paginas) >= 2
    for pagina in paginas:
        assert "Descripción" in pagina or "Total DOP" in pagina
    assert "Total DOP" in paginas[-1]
    assert sum("Total DOP" in p for p in paginas) == 1


def test_las_vinetas_se_reconocen_con_guion_o_punto():
    """El texto que se pega desde el PDF original trae guiones y a veces •."""
    assert cotizacion_pdf._celda_descripcion("- uno\n• dos\n* tres")
    parrafos = cotizacion_pdf._celda_descripcion("Título:\n- detalle\n\n- otro")
    assert len(parrafos) == 3            # las líneas vacías no generan renglón


# ------------------------------- RNC o Cédula
def test_el_tipo_de_documento_se_normaliza():
    from app import db

    assert db._tipo_documento("Cédula") == "Cédula"
    assert db._tipo_documento("cedula") == "Cédula"
    assert db._tipo_documento("CED") == "Cédula"
    assert db._tipo_documento("RNC") == "RNC"
    assert db._tipo_documento("") == "RNC"
    assert db._tipo_documento(None) == "RNC"


def test_la_cotizacion_con_cedula_lo_dice_en_el_pdf(db_path):
    from io import BytesIO

    from pypdf import PdfReader

    from app import cotizacion_pdf, db

    datos = cotizacion_pdf.calcular(_cotizacion()["items"], 4.5)
    with db.sesion() as c:
        con_rnc = db.guardar_cotizacion(c, {**datos, "numero": "000020", "fecha": "2026-11-24",
                                            "cliente_nombre": "PERSONA FISICA",
                                            "cliente_rnc": "001-1234567-8",
                                            "cliente_doc_tipo": "Cédula"})
        cot = db.obtener_cotizacion(c, con_rnc)

    assert cot["cliente_doc_tipo"] == "Cédula"
    texto = PdfReader(BytesIO(cotizacion_pdf.generar(cot))).pages[0].extract_text()
    assert "Cédula:" in texto and "001-1234567-8" in texto
    # el único "RNC:" que queda es el de la empresa en el pie de la plantilla
    assert texto.count("RNC:") == 1


def test_por_defecto_el_documento_es_rnc(db_path):
    from io import BytesIO

    from pypdf import PdfReader

    from app import cotizacion_pdf, db

    datos = cotizacion_pdf.calcular(_cotizacion()["items"], 4.5)
    with db.sesion() as c:
        nuevo = db.guardar_cotizacion(c, {**datos, "numero": "000021", "fecha": "2026-11-24",
                                          "cliente_nombre": "EMPRESA SRL",
                                          "cliente_rnc": "1-33-77201-9"})
        cot = db.obtener_cotizacion(c, nuevo)

    assert cot["cliente_doc_tipo"] == "RNC"
    texto = PdfReader(BytesIO(cotizacion_pdf.generar(cot))).pages[0].extract_text()
    assert "RNC:" in texto and "Cédula" not in texto


# ------------------------------- sin Itbis
def test_una_cotizacion_sin_itbis_no_cobra_impuesto():
    """El interruptor «Lleva Itbis» pone todos los renglones en 0."""
    cot = _cotizacion()
    cot["items"] = [{"descripcion": "Instalación eléctrica", "cantidad": 1,
                     "precio": 100000, "itbis": 0}]
    datos = cotizacion_pdf.calcular(cot["items"], 4.5)
    assert datos["itbis"] == 0
    assert datos["comision"] == 4500
    assert datos["subtotal"] == 104500       # la comisión va dentro del sub total
    assert datos["total"] == 104500


def test_el_formulario_tiene_el_interruptor_de_itbis_y_el_tipo_de_documento():
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "app" / "templates"
            / "cotizacion_nueva.html").read_text(encoding="utf-8")
    assert 'id="llevaItbis"' in html and "Lleva Itbis" in html
    assert 'value="Cédula"' in html and 'value="RNC"' in html
    assert "aplicarItbis" in html          # al apagarlo pone 0 en los renglones


# ------------------------------------------------ moneda, tasa y comisión
def test_la_comision_se_suma_al_subtotal_y_el_itbis_va_encima():
    """La comisión se aplica por unidad y el itbis se calcula sobre el sub total marcado."""
    items = [{"descripcion": "Panel", "cantidad": 1, "precio": 100000, "itbis": 18}]
    datos = cotizacion_pdf.calcular(items, comision_pct=10)
    assert datos["comision"] == 10000                    # 10% del bruto
    assert datos["subtotal"] == 110000                   # 100000 × 1.10
    assert datos["itbis"] == 19800                       # 18% sobre 110000
    assert datos["total"] == 129800


def test_la_comision_no_aparece_en_el_pdf():
    cot = _cotizacion()
    cot["comision_pct"] = 4.5
    texto = " ".join(_paginas(cotizacion_pdf.generar(cot)))
    assert "Comisión" not in texto and "Comision" not in texto


def test_cotizacion_en_dolares_usa_su_simbolo_y_no_rd():
    cot = _cotizacion()
    cot["moneda"] = "USD"
    cot["tasa_cambio"] = 60.5
    cot["items"] = [{"descripcion": "Sistema 5 kW", "cantidad": 1, "precio": 9000, "itbis": 18}]
    texto = " ".join(_paginas(cotizacion_pdf.generar(cot)))
    assert "US$ 9,405.00" in texto          # 9000 × 1.045 (comisión por unidad)
    assert "Total USD" in texto
    assert "Total DOP" not in texto
    assert "Dólares (USD) · RD$ 60.50" in texto    # la tasa que se usó


def test_cotizacion_en_pesos_dice_dop_y_no_dolares():
    cot = _cotizacion()
    cot["moneda"] = "DOP"
    texto = " ".join(_paginas(cotizacion_pdf.generar(cot)))
    assert "Total DOP" in texto and "Total USD" not in texto
    assert "US$" not in texto


def test_la_moneda_vieja_guardada_se_interpreta_bien():
    from app import db

    assert db.codigo_moneda("Pesos Dominicanos") == "DOP"
    assert db.codigo_moneda("Dólares (USD)") == "USD"
    assert db.codigo_moneda("USD") == "USD"
    assert db.codigo_moneda("") == "DOP"
    assert db.nombre_moneda("USD") == "Dólares (USD)"


def test_la_tasa_se_guarda_con_la_cotizacion(db_path):
    datos = cotizacion_pdf.calcular(_cotizacion()["items"], 4.5)
    with db.sesion() as c:
        nuevo = db.guardar_cotizacion(c, {**datos, "numero": "000060", "fecha": "2026-11-24",
                                          "cliente_nombre": "X", "moneda": "USD",
                                          "tasa_cambio": 59.75})
        cot = db.obtener_cotizacion(c, nuevo)
    assert cot["moneda"] == "USD" and round(cot["tasa_cambio"], 2) == 59.75
