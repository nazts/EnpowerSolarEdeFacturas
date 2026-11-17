"""PDF de cotización con el formato de Enpower Solar (sin la firma).

Replica la plantilla de la empresa: encabezado con el logo a la izquierda y los
datos de Genpower Solar a la derecha, bloque del cliente, tabla de productos con
Cantidad / Precio Uni / Itbis / Importe, y los totales con la banda roja del
Total DOP. La firma y el sello del coordinador NO se incluyen.
"""
from __future__ import annotations

from datetime import date

from fpdf import FPDF

AZUL = (31, 48, 95)          # azul marino del encabezado y las bandas
OCRE = (233, 160, 28)        # amarillo del logo
CLARO = (232, 237, 247)      # relleno alterno de las filas
ROJO = (237, 28, 36)         # banda del total
GRIS = (90, 90, 90)

EMPRESA = {
    "nombre": "GENPOWER SOLAR",
    "ciudad": "SANTIAGO, REP. DOM.",
    "rnc": "RNC: 1-33-77201-9",
    "correo": "info@genpowersolar.com.do",
}


def _t(texto) -> str:
    """fpdf con fuentes core solo maneja latin-1: se limpia lo que no entre."""
    return str(texto if texto is not None else "").encode("latin-1", "replace").decode("latin-1")


def _dinero(valor) -> str:
    try:
        return f"${float(valor or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


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
            tasa = float(item.get("itbis") if item.get("itbis") not in (None, "") else 18)
        except (TypeError, ValueError):
            tasa = 18.0
        base = round(cantidad * precio, 2)
        impuesto = round(base * tasa / 100, 2)
        subtotal += base
        itbis += impuesto
        limpios.append({
            "descripcion": str(item.get("descripcion") or "").strip(),
            "cantidad": cantidad, "precio": precio, "itbis_pct": tasa,
            "importe": round(base + impuesto, 2),
        })
    total = round(subtotal + itbis, 2)
    comision = round(total * (float(comision_pct or 0) / 100), 2)
    return {"items": limpios, "subtotal": round(subtotal, 2), "itbis": round(itbis, 2),
            "total": total, "comision_pct": float(comision_pct or 0), "comision": comision}


class CotizacionPDF(FPDF):
    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*GRIS)
        self.cell(0, 6, _t(f"Genpower Solar · Cotización · página {self.page_no()}"), 0, 0, "C")


def generar(cot: dict) -> bytes:
    """Devuelve el PDF en bytes. `cot` es el dict guardado en la base."""
    datos = calcular(cot.get("items") or [], cot.get("comision_pct", 4.5))
    pdf = CotizacionPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=22)
    pdf.set_margins(12, 12, 12)
    pdf.add_page()

    # ------------------------------------------------ encabezado: logo y empresa
    x_logo, y_logo = 14, 16
    pdf.set_xy(x_logo, y_logo)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(*AZUL)
    pdf.cell(70, 10, _t("ENPOWER"), 0, 2, "L")
    pdf.set_x(x_logo + 12)
    pdf.set_font("Helvetica", "", 17)
    pdf.cell(60, 9, _t("SOLAR"), 0, 2, "L")
    pdf.set_x(x_logo + 12)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(60, 6, _t("¡Del sol a tu favor!"), 0, 2, "L")

    pdf.set_xy(120, y_logo)
    for i, (texto, fuente) in enumerate(((EMPRESA["nombre"], "B"),
                                         (EMPRESA["ciudad"], "B"),
                                         (EMPRESA["rnc"], "B"))):
        pdf.set_font("Helvetica", fuente, 9)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(78, 5, _t(texto), 0, 2, "C")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*AZUL)
    pdf.cell(78, 5, _t(EMPRESA["correo"]), 0, 2, "C")

    # ------------------------------------------------ título y datos del documento
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, _t("Cotización"), 0, 1, "C")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 5.5, _t(f"No. Documento: {cot.get('numero') or '000001'}"), 0, 1, "C")
    pdf.cell(0, 5.5, _t(f"Fecha: {_fecha(cot.get('fecha'))}"), 0, 1, "C")
    pdf.cell(0, 5.5, _t(f"Moneda: {cot.get('moneda') or 'Pesos Dominicanos'}"), 0, 1, "C")

    # ------------------------------------------------ bloque del cliente
    pdf.ln(6)
    pdf.set_fill_color(*AZUL)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, _t("CLIENTE"), 0, 1, "C", True)
    pdf.ln(1.5)
    pdf.set_text_color(0, 0, 0)
    for etiqueta, valor in (("NOMBRE", cot.get("cliente_nombre")),
                            ("RNC", cot.get("cliente_rnc")),
                            ("DIRECCION", cot.get("cliente_direccion"))):
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(24, 5.5, _t(f"{etiqueta}:"), 0, 0, "L")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5.5, _t(valor or ""), 0, "L")
        pdf.set_x(pdf.l_margin)

    # ------------------------------------------------ tabla de productos
    pdf.ln(4)
    columnas = (("Descripción", 84, "L"), ("Cantidad", 22, "C"),
                ("Precio Uni", 28, "R"), ("Itbis", 20, "C"), ("Importe", 32, "R"))
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*AZUL)
    pdf.set_text_color(255, 255, 255)
    for titulo, ancho, alineacion in columnas:
        pdf.cell(ancho, 7, _t(titulo), 0, 0, alineacion, True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    renglones = datos["items"] or [{"descripcion": "", "cantidad": "", "precio": "",
                                    "itbis_pct": "", "importe": ""}]
    for i, item in enumerate(renglones):
        alto = 7
        if pdf.get_y() > 250:                      # salto de pagina antes de cortar
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(*AZUL)
            pdf.set_text_color(255, 255, 255)
            for titulo, ancho, alineacion in columnas:
                pdf.cell(ancho, 7, _t(titulo), 0, 0, alineacion, True)
            pdf.ln()
            pdf.set_text_color(0, 0, 0)
        fondo = i % 2 == 0
        pdf.set_fill_color(*CLARO)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_x(pdf.l_margin)
        pdf.cell(84, alto, _t(item["descripcion"]), 0, 0, "L", fondo)
        pdf.cell(22, alto, _t(f"{item['cantidad']:g}" if item["cantidad"] != "" else ""),
                 0, 0, "C", fondo)
        pdf.cell(28, alto, _t(_dinero(item["precio"]) if item["precio"] != "" else ""),
                 0, 0, "R", fondo)
        pdf.cell(20, alto, _t(f"{item['itbis_pct']:g}%" if item["itbis_pct"] != "" else ""),
                 0, 0, "C", fondo)
        pdf.cell(32, alto, _t(_dinero(item["importe"]) if item["importe"] != "" else ""),
                 0, 0, "R", fondo)
        pdf.ln()

    # relleno hasta completar el alto de la plantilla (10 renglones)
    for i in range(len(renglones), 10):
        pdf.set_fill_color(*CLARO)
        fondo = i % 2 == 0
        pdf.set_x(pdf.l_margin)
        for _, ancho, alineacion in columnas:
            pdf.cell(ancho, 7, "", 0, 0, alineacion, fondo)
        pdf.ln()

    # ------------------------------------------------ totales
    pdf.ln(3)
    x_totales = 130
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_x(x_totales)
    pdf.cell(40, 7, _t("Sub. Total"), 0, 0, "R")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(40, 7, _t(_dinero(datos["subtotal"])), 0, 1, "R")

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_x(x_totales)
    pdf.cell(40, 7, _t("Itbis"), 0, 0, "R")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(40, 7, _t(_dinero(datos["itbis"])), 0, 1, "R")

    pdf.set_x(x_totales)
    pdf.set_fill_color(*ROJO)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(40, 7, _t("Total DOP"), 0, 0, "R", True)
    pdf.cell(40, 7, _t(_dinero(datos["total"])), 0, 1, "R", True)
    pdf.set_text_color(0, 0, 0)

    if datos["comision_pct"]:
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*GRIS)
        pdf.cell(0, 6, _t(f"Comisión {datos['comision_pct']:g}%: {_dinero(datos['comision'])}"),
                 0, 1, "R")

    if cot.get("notas"):
        pdf.ln(6)
        pdf.set_x(pdf.l_margin)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, _t("Notas"), 0, 1, "L")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5.5, _t(cot["notas"]), 0, "L")

    pdf.ln(14)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GRIS)
    pdf.cell(0, 5, _t(f"Documento generado el {date.today().strftime('%d/%m/%Y')} · "
                      "válido por 15 días"), 0, 1, "C")

    return bytes(pdf.output())
