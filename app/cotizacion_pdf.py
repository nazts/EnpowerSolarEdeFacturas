"""PDF de cotización sobre la plantilla de la empresa (PIE DE PÁGINA.pdf).

El documento se arma en dos capas: la plantilla (logo, marca de agua y pie con
los datos de contacto) queda de fondo y encima se dibuja el contenido con
reportlab; pypdf fusiona las capas. Ventajas frente a dibujar todo a mano:

- la marca (logo, isotipo, pie) se mantiene **idéntica** al archivo del diseñador;
- si la cotización tiene muchos renglones, la tabla continúa en otra hoja con la
  misma plantilla y el encabezado de columnas repetido;
- la firma es opcional: se dibuja SOLO si la cotización eligió una firma (o sello)
  de la tabla `firmas` y esa firma tiene una posición guardada (x e y). No hay
  imagen ni coordenadas por defecto. Sin firma, queda la línea para firmar a mano
  con el nombre y el cargo de la propia cotización o de la firma.

Configuración (`.env`, opcional):

    COTIZACION_PLANTILLA=app/static/plantillas/cotizacion.pdf
"""
from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle
from xml.sax.saxutils import escape

from . import config

# Paleta del Manual de Marca 2026
VERDE = colors.HexColor("#26412D")
DORADO = colors.HexColor("#BB894C")
CREMA = colors.HexColor("#FAF3E9")
BORDE = colors.HexColor("#BFC8C2")
TEXTO = colors.HexColor("#1F2922")
TENUE = colors.HexColor("#5A665E")

RAIZ_STATIC = Path(__file__).resolve().parent / "static"
FUENTES = RAIZ_STATIC / "fonts" / "pdf"

# Medidas de la plantilla (carta 612 x 791 pt): el logo ocupa la esquina
# superior izquierda y el pie llega hasta y ≈ 84 pt.
MARGEN = 50
Y_CAJAS_ARRIBA = 630        # cajas de cliente y documento
ALTO_CAJA = 84
Y_DIVISOR = 536
Y_TABLA_P1 = 528            # debajo de las cajas
Y_TABLA_CONT = 620          # continuaciones: debajo del logo
Y_LIMITE = 100              # no bajar de aquí (empieza el pie de la plantilla)
ALTO_CIERRE = 118           # totales + firma + notas, siempre al pie de la hoja
Y_CIERRE = Y_LIMITE + ALTO_CIERRE   # 218: fin de la tabla y arriba del cierre
Y_TABLA_FIN = Y_CIERRE      # la tabla nunca invade el bloque de cierre


SIMBOLOS = {"DOP": "RD$", "USD": "US$"}


def _codigo_moneda(valor) -> str:
    """'DOP' o 'USD'. Acepta también el nombre viejo («Pesos Dominicanos»)."""
    texto = str(valor or "").strip().lower()
    if texto.startswith(("usd", "us$", "dolar", "dólar", "dolares", "dólares", "us")):
        return "USD"
    return "DOP"


def _texto_moneda(cot: dict) -> str:
    """«Pesos Dominicanos (DOP)» o «Dólares (USD) · RD$ 60.50»."""
    if _codigo_moneda(cot.get("moneda")) != "USD":
        return "Pesos Dominicanos (DOP)"
    tasa = float(cot.get("tasa_cambio") or 0)
    return f"Dólares (USD) · RD$ {tasa:,.2f}" if tasa > 0 else "Dólares (USD)"


def _dinero(valor, moneda: str = "DOP") -> str:
    simbolo = SIMBOLOS.get(_codigo_moneda(moneda), "RD$")
    try:
        return f"{simbolo} {float(valor or 0):,.2f}"
    except (TypeError, ValueError):
        return f"{simbolo} 0.00"


def _fecha(valor: str | None) -> str:
    texto = str(valor or "")[:10]
    try:
        anio, mes, dia = texto.split("-")
        return f"{dia}/{mes}/{anio}"
    except ValueError:
        return texto


def calcular(items: list[dict], comision_pct: float = 4.5) -> dict:
    """Suma renglones: cantidad x precio, con itbis por linea (18% por defecto)."""
    subtotal = itbis = 0.0
    limpios = []
    for item in items or []:
        try:
            cantidad = float(item.get("cantidad") or 0)
        except (TypeError, ValueError):
            cantidad = 0.0
        try:
            precio = float(item.get("precio") or 0)
        except (TypeError, ValueError):
            precio = 0.0
        try:
            tasa = float(item.get("itbis") if item.get("itbis") is not None else 18)
        except (TypeError, ValueError):
            tasa = 18.0
        base = cantidad * precio
        impuesto = base * tasa / 100
        subtotal += base
        itbis += impuesto
        limpios.append({
            "descripcion": str(item.get("descripcion") or "").strip(),
            "cantidad": cantidad, "precio": precio, "itbis_pct": tasa,
            "itbis_monto": impuesto,
            "importe": round(base + impuesto, 2),
        })
    # El total que paga el cliente es sub total + itbis. La comisión es un dato
    # interno (se guarda para el control de ventas) y no se suma ni se imprime.
    total = subtotal + itbis
    comision = subtotal * (comision_pct or 0) / 100
    return {
        "items": limpios,
        "subtotal": round(subtotal, 2),
        "itbis": round(itbis, 2),
        "comision_pct": comision_pct,
        "comision": round(comision, 2),
        "total": round(total, 2),
    }


# --------------------------------------------------------------------- fuentes
def _registrar_fuentes() -> None:
    """Registra Archivo y Genos para reportlab (una sola vez por proceso)."""
    nombres = pdfmetrics.getRegisteredFontNames()
    if "Archivo" not in nombres:
        pdfmetrics.registerFont(TTFont("Archivo", str(FUENTES / "Archivo-Regular.ttf")))
        pdfmetrics.registerFont(TTFont("Archivo-Semi", str(FUENTES / "Archivo-SemiBold.ttf")))
    if "Genos" not in nombres:
        pdfmetrics.registerFont(TTFont("Genos", str(FUENTES / "Genos-Light.ttf")))
        pdfmetrics.registerFont(TTFont("Genos-Semi", str(FUENTES / "Genos-SemiBold.ttf")))


# ------------------------------------------------------------------ contenido
def _encabezado(c: canvas.Canvas, cot: dict) -> None:
    """Título del documento y cajas de cliente / datos."""
    ancho, _ = c._pagesize
    derecha = ancho - MARGEN
    _registrar_fuentes()

    # --- caja del cliente (izquierda)
    caja_w, caja_h = 295, ALTO_CAJA
    y0 = Y_CAJAS_ARRIBA - caja_h
    c.setFillColor(colors.Color(0.98, 0.99, 0.98, alpha=0.92))
    c.setStrokeColor(BORDE)
    c.setLineWidth(0.7)
    c.roundRect(MARGEN, y0, caja_w, caja_h, 5, stroke=1, fill=1)

    c.setFillColor(VERDE)
    c.setFont("Archivo-Semi", 9)
    c.drawString(MARGEN + 11, Y_CAJAS_ARRIBA - 17, "DATOS DEL CLIENTE")

    tipo_doc = ("Cédula" if str(cot.get("cliente_doc_tipo") or "").strip().lower()
                .startswith(("ced", "céd")) else "RNC")
    filas = (("Nombre:", cot.get("cliente_nombre") or ""),
             (f"{tipo_doc}:", cot.get("cliente_rnc") or ""),
             ("Dirección:", cot.get("cliente_direccion") or ""))
    for i, (etiqueta, valor) in enumerate(filas):
        y = Y_CAJAS_ARRIBA - 35 - i * 18
        c.setFillColor(TEXTO)
        c.setFont("Archivo-Semi", 8.5)
        c.drawString(MARGEN + 11, y, etiqueta)
        texto = str(valor)
        # se recorta lo que no entra en la caja (una dirección larga)
        while texto and pdfmetrics.stringWidth(texto, "Archivo", 8.5) > caja_w - 78:
            texto = texto[:-2]
        c.setFont("Archivo", 8.5)
        c.drawString(MARGEN + 62, y, texto)

    # --- caja del documento (derecha)
    doc_w = 205
    doc_x = derecha - doc_w
    c.setFillColor(colors.Color(0.98, 0.99, 0.98, alpha=0.92))
    c.setStrokeColor(BORDE)
    c.roundRect(doc_x, y0, doc_w, caja_h, 5, stroke=1, fill=1)

    c.setFillColor(VERDE)
    c.setFont("Archivo-Semi", 12)
    c.drawString(doc_x + 11, Y_CAJAS_ARRIBA - 18, "COTIZACIÓN")
    c.setFillColor(DORADO)
    c.setFont("Archivo-Semi", 9.5)
    c.drawRightString(doc_x + doc_w - 11, Y_CAJAS_ARRIBA - 18,
                      f"No. {cot.get('numero') or ''}")

    datos = (("No. de documento:", str(cot.get("numero") or "")),
             ("Fecha:", _fecha(cot.get("fecha"))),
             ("Moneda:", _texto_moneda(cot)))
    for i, (etiqueta, valor) in enumerate(datos):
        y = Y_CAJAS_ARRIBA - 37 - i * 17
        c.setFillColor(TEXTO)
        c.setFont("Archivo-Semi", 8)
        c.drawString(doc_x + 11, y, etiqueta)
        c.setFont("Archivo", 8)
        c.drawRightString(doc_x + doc_w - 11, y, valor)

    # --- divisor dorado
    c.setStrokeColor(DORADO)
    c.setLineWidth(1.4)
    c.line(MARGEN, Y_DIVISOR, derecha, Y_DIVISOR)


def _estilos_descripcion() -> tuple[ParagraphStyle, ParagraphStyle]:
    """Estilos de la celda de descripción: título y viñetas con sangría colgante."""
    titulo = ParagraphStyle("desc-titulo", fontName="Archivo-Semi", fontSize=8.4, leading=11,
                            spaceAfter=1.5, textColor=TEXTO)
    vineta = ParagraphStyle("desc-vineta", fontName="Archivo", fontSize=8.2, leading=10.6,
                            leftIndent=9, bulletIndent=1, bulletFontName="Archivo",
                            bulletFontSize=8.2, textColor=TEXTO, spaceAfter=0.5)
    return titulo, vineta


def _celda_descripcion(texto: str) -> list:
    """Convierte la descripción en párrafos: así envuelve y las viñetas alinean.

    Un texto largo en una sola celda se veía apretado y las líneas que envolvían
    quedaban pegadas al borde: con párrafos, la segunda línea de cada viñeta
    arranca sangrada y el renglón respira.
    """
    titulo, vineta = _estilos_descripcion()
    parrafos = []
    for crudo in str(texto or "").splitlines():
        linea = crudo.strip()
        if not linea:
            continue
        if linea.startswith(("-", "•", "*")):
            parrafos.append(Paragraph(escape(linea[1:].strip()), vineta, bulletText="•"))
        else:
            parrafos.append(Paragraph(escape(linea), titulo))
    return parrafos or [Paragraph("", titulo)]


def _tabla(datos: dict, moneda: str = "DOP") -> Table:
    """Tabla de renglones lista para dibujar (con encabezado repetible)."""
    filas = [["Descripción", "Cantidad", "Precio unitario", "Itbis", "Importe"]]
    for item in datos["items"] or [{"descripcion": ""}]:
        filas.append([
            _celda_descripcion(item.get("descripcion") or ""),
            f"{item['cantidad']:g}" if item.get("cantidad") not in (None, "") else "",
            _dinero(item["precio"], moneda) if item.get("precio") not in (None, "") else "",
            _dinero(item["itbis_monto"], moneda) if item.get("itbis_monto") not in (None, "") else "",
            _dinero(item["importe"], moneda) if item.get("importe") not in (None, "") else "",
        ])
    # splitInRow: un renglón muy alto con muchas viñetas se parte entre hojas
    # en vez de dejar media hoja en blanco
    tabla = Table(filas, colWidths=[248, 48, 76, 62, 78], repeatRows=1, splitInRow=1)
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), VERDE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Archivo-Semi"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("FONTNAME", (0, 1), (-1, -1), "Archivo"),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), TEXTO),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.6, BORDE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CREMA]),
    ]))
    return tabla


def _totales(c: canvas.Canvas, datos: dict, y_top: float,
             moneda: str = "DOP", tasa: float = 1) -> None:
    """Cuadro de totales: sub total, itbis y total, en la moneda de la cotización.

    La comisión es información interna del sistema: **no** se dibuja ni se suma.
    Cuando la cotización es en dólares se indica la tasa usada y el equivalente.
    """
    ancho, _ = c._pagesize
    derecha = ancho - MARGEN
    tot_w = 215
    codigo = _codigo_moneda(moneda)
    filas = [["Sub. Total", _dinero(datos["subtotal"], codigo)],
             ["Itbis", _dinero(datos["itbis"], codigo)],
             [f"Total {codigo}", _dinero(datos["total"], codigo)]]
    tabla = Table(filas, colWidths=[95, 120], rowHeights=[21, 21, 25])
    tabla.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, 1), "Archivo-Semi"),
        ("FONTNAME", (1, 0), (1, 1), "Archivo"),
        ("FONTSIZE", (0, 0), (-1, 1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 1), TEXTO),
        ("BACKGROUND", (0, 2), (-1, 2), VERDE),
        ("TEXTCOLOR", (0, 2), (-1, 2), colors.white),
        ("FONTNAME", (0, 2), (0, 2), "Archivo-Semi"),
        ("FONTNAME", (1, 2), (1, 2), "Archivo-Semi"),
        ("FONTSIZE", (0, 2), (-1, 2), 11),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.6, BORDE),
    ]))
    _, alto = tabla.wrapOn(c, tot_w, 200)
    tabla.drawOn(c, derecha - tot_w, y_top - alto)




def _alto_imagen(ruta: Path, ancho: float) -> float:
    """Alto que le corresponde a la imagen para no deformarla."""
    from PIL import Image

    try:
        with Image.open(ruta) as img:
            ancho_px, alto_px = img.size
        return ancho * alto_px / ancho_px if ancho_px else ancho * 0.35
    except Exception:  # noqa: BLE001 - si no se puede leer, se usa una proporción típica
        return ancho * 0.35


_CAJA_TINTA: dict[str, tuple[float, float]] = {}      # ruta -> (mtime, borde)


def _borde_inferior_tinta(ruta: Path) -> float:
    """Fracción del alto donde termina la tinta de la imagen (0 = arriba, 1 = abajo).

    Un PNG puede traer transparencia de sobra: si la línea se dibuja en el borde
    del archivo, la firma queda «flotando» arriba de la línea. Con esto la línea
    se apoya en la última marca de tinta.
    """
    try:
        marca = ruta.stat().st_mtime
        guardado = _CAJA_TINTA.get(str(ruta))
        if guardado and guardado[0] == marca:
            return guardado[1]

        from PIL import Image

        with Image.open(ruta) as imagen:
            caja = imagen.convert("RGBA").getchannel("A").getbbox()
            alto = imagen.height or 1
        borde = (caja[3] / alto) if caja else 1.0
        _CAJA_TINTA[str(ruta)] = (marca, borde)
        return borde
    except Exception:  # noqa: BLE001 - sin imagen legible, borde del archivo
        return 1.0


def _firma(c: canvas.Canvas, nombre: str = "", firma: dict | None = None) -> None:
    """Línea de firma y, si la cotización eligió una firma CON posición, su imagen.

    `firma` viene de la tabla `firmas`: archivo, nombre, cargo, ancho, x e y en
    puntos PDF. La imagen se dibuja SOLO si hay posición elegida (x e y): no hay
    coordenadas por defecto. Cuando hay imagen, el nombre y el cargo se imprimen
    debajo, centrados. Sin imagen queda la línea para firmar a mano con el nombre
    debajo (el nombre y el cargo salen de la cotización o de la firma).
    """
    _registrar_fuentes()
    x0, ancho_guia = MARGEN + 10, 160
    y_linea = Y_LIMITE + 30

    ruta = None
    ancho = ancho_guia
    x_img = y_img = None

    if firma and firma.get("archivo"):
        ancho = float(firma.get("ancho") or ancho_guia)
        # sin posición elegida no se dibuja la imagen: no hay "por defecto"
        if firma.get("x") is not None and firma.get("y") is not None:
            x_img, y_img = float(firma["x"]), float(firma["y"])
            candidata = RAIZ_STATIC / "firmas" / str(firma["archivo"])
            if candidata.exists():
                ruta = candidata

    alto = 0.0
    if ruta is not None:
        alto = _alto_imagen(ruta, ancho)
        try:
            # anchor="sw": (x, y) es la esquina inferior izquierda, igual que en el
            # editor. Con el centro, la firma salía corrida media imagen hacia arriba.
            c.drawImage(ImageReader(str(ruta)), x_img, y_img, width=ancho, height=alto,
                        mask="auto", preserveAspectRatio=False, anchor="sw")
        except Exception:  # noqa: BLE001 - una firma rota no debe tumbar la cotización
            pass

    nombre = (nombre or "").strip()
    if firma:
        nombre = nombre or str(firma.get("nombre") or "").strip()
    cargo = str(firma.get("cargo") or "").strip() if firma else ""

    if ruta is None:
        # sin imagen: la línea clásica para firmar a mano
        c.setStrokeColor(TEXTO)
        c.setLineWidth(0.8)
        c.line(x0 - 15, y_linea, x0 + ancho_guia + 15, y_linea)
        centro, y = x0 + ancho_guia / 2, y_linea - 13
        if nombre:
            c.setFillColor(TEXTO)
            c.setFont("Archivo-Semi", 8.5)
            c.drawCentredString(centro, y, nombre)
            y -= 11
        if cargo:
            c.setFillColor(TENUE)
            c.setFont("Archivo", 8)
            c.drawCentredString(centro, y, cargo)
        return

    # con imagen: la línea guía se apoya en la última tinta de la firma (no en el
    # borde del archivo, que puede traer transparencia de sobra) y el nombre va
    # debajo de la línea, como en un documento firmado a mano
    centro = x_img + ancho / 2
    bajo = _borde_inferior_tinta(ruta) if ruta else 1.0
    y_linea_img = y_img + alto * (1 - bajo) + 0.5
    c.setStrokeColor(TEXTO)
    c.setLineWidth(0.8)
    c.line(x_img - 12, y_linea_img, x_img + ancho + 12, y_linea_img)

    y = y_linea_img - 11
    if nombre:
        c.setFillColor(TEXTO)
        c.setFont("Archivo-Semi", 8.5)
        c.drawCentredString(centro, y, nombre)
        y -= 10.5
    if cargo:
        c.setFillColor(TENUE)
        c.setFont("Archivo", 7.8)
        c.drawCentredString(centro, y, cargo)


def _notas(c: canvas.Canvas, notas: str) -> None:
    """Notas del pie: a la derecha, debajo de los totales (no pisan la firma)."""
    if not notas:
        return
    ancho, _ = c._pagesize
    x = ancho - MARGEN - 215
    y = Y_CIERRE - 78
    c.setFillColor(TEXTO)
    c.setFont("Archivo-Semi", 8)
    c.drawString(x, y, "Notas")
    c.setFont("Archivo", 8)
    c.setFillColor(TENUE)
    for i, linea in enumerate(simpleSplit(str(notas), "Archivo", 8, 215)[:3]):
        c.drawString(x, y - 11 - i * 9.5, linea)


def _cierre(c: canvas.Canvas, cot: dict, datos: dict) -> None:
    """Totales, firma y notas, siempre en el mismo lugar del pie de la hoja."""
    _totales(c, datos, Y_CIERRE, cot.get("moneda") or "DOP",
             float(cot.get("tasa_cambio") or 1))
    _firma(c, str(cot.get("firma") or ""), cot.get("firma_imagen"))
    _notas(c, cot.get("notas") or "")


# ------------------------------------------------------------------- generar
def _firma_elegida(cot: dict) -> dict | None:
    """Trae la firma o sello guardado que eligió la cotización (si hay base)."""
    if cot.get("firma_imagen"):
        return cot["firma_imagen"]
    firma_id = cot.get("firma_id")
    if not firma_id:
        return None
    try:
        from app import db

        with db.sesion() as conn:
            return db.obtener_firma(conn, int(firma_id))
    except Exception:  # noqa: BLE001 - sin base, la cotización sale sin sello
        return None


def _avisa_si_falta_el_archivo(imagen: dict | None) -> None:
    """Si la firma elegida no está en el servidor, queda anotado en Actividad."""
    if not imagen or not imagen.get("archivo"):
        return
    if (RAIZ_STATIC / "firmas" / str(imagen["archivo"])).exists():
        return
    try:
        from app import db

        with db.sesion() as conn:
            db.log_evento(conn, "aviso", "La imagen de la firma no está en el servidor",
                          f"archivo {imagen['archivo']} (firma {imagen.get('nombre') or imagen.get('id')})")
    except Exception:  # noqa: BLE001 - el aviso nunca debe tumbar el PDF
        pass


def generar(cot: dict) -> bytes:
    """Devuelve el PDF (una o más hojas) sobre la plantilla de la empresa."""
    imagen = _firma_elegida(cot)
    _avisa_si_falta_el_archivo(imagen)
    if imagen and cot.get("firma_x") is not None and cot.get("firma_y") is not None:
        # la cotización puede tener su propia posición (se firmó desde el listado)
        imagen = {**imagen, "x": cot["firma_x"], "y": cot["firma_y"],
                  "ancho": cot.get("firma_ancho") or imagen.get("ancho")}
    cot = {**cot, "firma_imagen": imagen}
    datos = calcular(cot.get("items") or [], cot.get("comision_pct", 4.5))
    plantilla = PdfReader(str(config.PLANTILLA_COTIZACION))
    base = plantilla.pages[0]
    ancho = float(base.mediabox.width)
    alto = float(base.mediabox.height)
    ruta_plantilla = str(config.PLANTILLA_COTIZACION)
    ancho_tabla = ancho - 2 * MARGEN

    capas: list[tuple[canvas.Canvas, io.BytesIO]] = []

    def nueva_pagina(con_encabezado: bool) -> canvas.Canvas:
        buffer = io.BytesIO()
        capa = canvas.Canvas(buffer, pagesize=(ancho, alto))
        if con_encabezado:
            _encabezado(capa, cot)
        capas.append((capa, buffer))
        return capa

    capa = nueva_pagina(True)
    resto = _tabla(datos, cot.get("moneda") or "DOP")
    y = Y_TABLA_P1

    while True:
        # Primero se intenta cerrar aquí (dejando sitio a totales y firma).
        espacio_cierre = y - Y_TABLA_FIN
        alto_resto = resto.wrapOn(capa, ancho_tabla, espacio_cierre)[1]
        if alto_resto <= espacio_cierre:
            resto.drawOn(capa, MARGEN, y - alto_resto)
            break

        # No cabe con el cierre: la hoja se aprovecha entera hasta el pie y
        # el bloque de cierre se va a la última hoja.
        espacio = y - Y_LIMITE
        partes = resto.split(ancho_tabla, espacio)
        if not partes:                    # ni una fila entra: se corta igual
            alto_resto = resto.wrapOn(capa, ancho_tabla, espacio)[1]
            resto.drawOn(capa, MARGEN, y - alto_resto)
            break
        parte = partes[0]
        resto = partes[1] if len(partes) > 1 else None
        alto_parte = parte.wrapOn(capa, ancho_tabla, espacio)[1]
        parte.drawOn(capa, MARGEN, y - alto_parte)

        if resto is None:
            break

        capa.showPage()
        capa = nueva_pagina(False)
        y = Y_TABLA_CONT

    _cierre(capa, cot, datos)

    # fusionar cada capa sobre una copia de la plantilla.
    # pypdf 7 exige que la página pertenezca a un writer: se agrega la plantilla
    # al documento y recién ahí se le superpone la capa.
    escritor = PdfWriter()
    for capa, buffer in capas:
        capa.showPage()
        capa.save()
        buffer.seek(0)
        escritor.append(PdfReader(ruta_plantilla))
        escritor.pages[-1].merge_page(PdfReader(buffer).pages[0])

    salida = io.BytesIO()
    escritor.write(salida)
    return salida.getvalue()
