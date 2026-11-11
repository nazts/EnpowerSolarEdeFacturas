"""Exportacion a Excel (contratos + facturas)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import config
from .db import conectar, init_db

ENCABEZADO = Font(bold=True, color="FFFFFF")
FONDO = PatternFill("solid", fgColor="1F4E78")


def _hoja(ws, columnas: list[str], filas: list[list]) -> None:
    ws.append(columnas)
    for celda in ws[1]:
        celda.font = ENCABEZADO
        celda.fill = FONDO
        celda.alignment = Alignment(horizontal="center")
    for fila in filas:
        ws.append(fila)
    for i, col in enumerate(columnas, start=1):
        ancho = max([len(str(col))] + [len(str(f[i - 1] or "")) for f in filas] or [10])
        ws.column_dimensions[get_column_letter(i)].width = min(max(ancho + 2, 12), 45)
    ws.freeze_panes = "A2"


def exportar(destino: str | Path | None = None) -> Path:
    init_db()
    conn = conectar()
    contratos = conn.execute(
        """SELECT nic, razon_social, nombre_normalizado, alias, asesor, dia_corte,
                  activo, notas, paneles, potencia_paneles, inversores, bateria,
                  capacidad_bateria FROM contratos ORDER BY nic"""
    ).fetchall()
    facturas = conn.execute(
        """SELECT f.nic, c.nombre_normalizado, c.alias, c.asesor, f.no_factura, f.ref_pago,
                  f.tipo, f.periodo_ini, f.periodo_fin, f.corte, f.emision, f.vence,
                  f.dias, f.kwh, f.sin_subsidio, f.subsidio, f.facturado, f.estado,
                  f.enviada, f.pdf_path
           FROM facturas f LEFT JOIN contratos c ON c.nic = f.nic
           WHERE f.estado = 'descargada'
           ORDER BY f.corte DESC, f.nic"""
    ).fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active or wb.create_sheet("Contratos")
    ws.title = "Contratos"
    _hoja(ws, ["NIC", "Razón social (factura)", "Nombre normalizado", "Alias / pagador",
               "Asesor", "Día de corte", "Activo", "Notas", "Paneles",
               "Capacidad instalada (kW)", "Inversores", "Batería", "Capacidad batería (kWh)"],
          [[c["nic"], c["razon_social"], c["nombre_normalizado"], c["alias"], c["asesor"],
            c["dia_corte"], "Sí" if c["activo"] else "No", c["notas"], c["paneles"],
            c["potencia_paneles"], c["inversores"], c["bateria"], c["capacidad_bateria"]]
           for c in contratos])

    ws2 = wb.create_sheet("Facturas")
    _hoja(ws2, ["NIC", "Nombre normalizado", "Alias / pagador", "Asesor", "No. Factura",
                "Ref. Pago", "Tipo", "Período inicio", "Período fin", "Corte", "Emisión",
                "Vence", "Días", "kWh", "Sin subsidio", "Subsidio", "Facturado", "Estado",
                "Enviada", "Archivo"],
          [[f["nic"], f["nombre_normalizado"], f["alias"], f["asesor"], f["no_factura"],
            f["ref_pago"], f["tipo"], f["periodo_ini"], f["periodo_fin"], f["corte"],
            f["emision"], f["vence"], f["dias"], f["kwh"], f["sin_subsidio"], f["subsidio"],
            f["facturado"], f["estado"], "Sí" if f["enviada"] else "No",
            Path(f["pdf_path"]).name if f["pdf_path"] else ""] for f in facturas])

    dest = Path(destino) if destino else (
        config.EXPORT_DIR / f"facturas_enpower_{datetime.now():%Y%m%d_%H%M}.xlsx")
    dest.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dest)
    return dest


if __name__ == "__main__":
    print(exportar())
