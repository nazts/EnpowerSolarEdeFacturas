"""PDF de cotización sobre la plantilla de la empresa (PIE DE PÁGINA.pdf).

El documento se arma en dos capas: la plantilla (logo, marca de agua y pie con
los datos de contacto) queda de fondo y encima se dibuja el contenido con
reportlab; pypdf fusiona las capas. Ventajas frente a dibujar todo a mano:

- la marca (logo, isotipo, pie) se mantiene **idéntica** al archivo del diseñador;
- si la cotización tiene muchos renglones, la tabla continúa en otra hoja con la
  misma plantilla y el encabezado de columnas repetido;
- la firma es opcional: si hay un PNG con fondo transparente se coloca sobre la
  línea, y si no, la hoja queda con la línea para firmar a mano (el nombre y el
  cargo se toman del `.env`, nunca escritos en el código).

Configuración (`.env`, opcional):

    COTIZACION_PLANTILLA=app/static/plantillas/cotizacion.pdf
    COTIZACION_FIRMA=app/static/plantillas/firma.png
    COTIZACION_FIRMA_NOMBRE=Nombre Apellido
    COTIZACION_FIRMA_CARGO=Cargo
"""
from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from . import config

# Paleta del Manual de Marca 2026
VERDE = colors.HexColor("#26412D")
DORADO = colors.HexColor("#BB894C")
CREMA = colors.HexColor("#FAF3E9")
BORDE = colors.HexColor("#BFC8C2")
TEXTO = colors.HexColor("#1F2922")
TENUE = colors.HexColor("#5A665E")

FUENTES = Path(__file__).resolve().parent / "static" / "fonts" / "pdf"

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


def _dinero(valor) -> str:
    try:
        return f"RD$ {float(valor or 0):,.2f}"
    except (TypeError, ValueError):
        return "RD$ 0.00"


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
    total = subtotal + itbis
    comision = total * (comision_pct or 0) / 100
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

    filas = (("Nombre:", cot.get("cliente_nombre") or ""),
             ("RNC:", cot.get("cliente_rnc") or ""),
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
             ("Moneda:", str(cot.get("moneda") or "Pesos Dominicanos")))
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


def _tabla(datos: dict) -> Table:
    """Tabla de renglones lista para dibujar (con encabezado repetible)."""
    filas = [["Descripción", "Cantidad", "Precio unitario", "Itbis", "Importe"]]
    for item in datos["items"] or [{"descripcion": ""}]:
        filas.append([
            item.get("descripcion") or "",
            f"{item['cantidad']:g}" if item.get("cantidad") not in (None, "") else "",
            _dinero(item["precio"]) if item.get("precio") not in (None, "") else "",
            _dinero(item["itbis_monto"]) if item.get("itbis_monto") not in (None, "") else "",
            _dinero(item["importe"]) if item.get("importe") not in (None, "") else "",
        ])
    tabla = Table(filas, colWidths=[222.3, 55, 78, 72, 85], repeatRows=1)
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
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.6, BORDE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CREMA]),
    ]))
    return tabla


def _totales(c: canvas.Canvas, datos: dict, y_top: float) -> None:
    """Dibuja el cuadro de totales a la derecha."""
    ancho, _ = c._pagesize
    derecha = ancho - MARGEN
    tot_w = 215
    filas = [["Sub. Total", _dinero(datos["subtotal"])],
             ["Itbis", _dinero(datos["itbis"])],
             ["Total DOP", _dinero(datos["total"])]]
    tabla = Table(filas, colWidths=[95, 120], rowHeights=[21, 21, 25])
    tabla.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, 1), "Archivo-Semi"),
        ("FONTNAME", (1, 0), (1, 1), "Genos"),
        ("FONTSIZE", (0, 0), (-1, 1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 1), TEXTO),
        ("BACKGROUND", (0, 2), (-1, 2), VERDE),
        ("TEXTCOLOR", (0, 2), (-1, 2), colors.white),
        ("FONTNAME", (0, 2), (0, 2), "Archivo-Semi"),
        ("FONTNAME", (1, 2), (1, 2), "Genos-Semi"),
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


def _firma(c: canvas.Canvas, nombre: str = "") -> None:
    """Línea de firma, la imagen PNG si existe y el nombre de quien firma."""
    x0, ancho = MARGEN + 10, 160
    y_linea = Y_LIMITE + 30
    centro = x0 + ancho / 2
    _registrar_fuentes()

    ruta = Path(config.FIRMA_COTIZACION)
    if ruta.exists():
        try:
            c.drawImage(ImageReader(str(ruta)), x0, y_linea + 3, width=ancho, height=55,
                        mask="auto", preserveAspectRatio=True, anchor="c")
        except Exception:  # noqa: BLE001 - una firma rota no debe tumbar la cotización
            pass

    c.setStrokeColor(TEXTO)
    c.setLineWidth(0.8)
    c.line(x0 - 15, y_linea, x0 + ancho + 15, y_linea)

    y = y_linea - 13
    nombre = (nombre or "").strip() or config.FIRMA_NOMBRE
    if nombre:
        c.setFillColor(TEXTO)
        c.setFont("Archivo-Semi", 8.5)
        c.drawCentredString(centro, y, nombre)
        y -= 11
    if config.FIRMA_CARGO:
        c.setFillColor(TENUE)
        c.setFont("Archivo", 8)
        c.drawCentredString(centro, y, config.FIRMA_CARGO)


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
    _totales(c, datos, Y_CIERRE)
    _firma(c, str(cot.get("firma") or ""))
    _notas(c, cot.get("notas") or "")


# ------------------------------------------------------------------- generar
def generar(cot: dict) -> bytes:
    """Devuelve el PDF (una o más hojas) sobre la plantilla de la empresa."""
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
    resto = _tabla(datos)
    y = Y_TABLA_P1

    while True:
        espacio = y - Y_TABLA_FIN
        alto_resto = resto.wrapOn(capa, ancho_tabla, espacio)[1]
        if alto_resto <= espacio:
            resto.drawOn(capa, MARGEN, y - alto_resto)
            break

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
