"""Repara datos de facturas ya guardadas.

Se usa cuando una factura quedo sin fecha de corte (por ejemplo PDFs leidos con
el encoding equivocado: "Días" llegaba como "DÃ­as" y no se reconocia el periodo).

    python -m app.reparar              revisa y corrige (no toca Telegram)
    python -m app.reparar --simular    solo informa, no escribe
    python -m app.reparar --nic 6501165
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db
from .pdfdata import datos_de_archivo


def reparar(simular: bool = False, nic: str | None = None) -> dict:
    """Vuelve a leer el PDF guardado y completa corte/periodo/vencimiento."""
    res = {"revisadas": 0, "corregidas": 0, "sin_pdf": 0, "sin_datos": 0,
           "contratos_actualizados": 0, "detalle": []}

    with db.sesion() as conn:
        # facturas no guarda el proveedor: se toma del contrato
        sql = ("SELECT f.id, f.nic, f.ref_pago, f.pdf_path, "
               "COALESCE(c.proveedor, 'edenorte') AS proveedor "
               "FROM facturas f LEFT JOIN contratos c ON c.nic = f.nic "
               "WHERE f.corte IS NULL AND f.estado = 'descargada'")
        params: list = []
        if nic:
            sql += " AND f.nic = ?"
            params.append(nic)
        filas = conn.execute(sql, params).fetchall()

        for f in filas:
            res["revisadas"] += 1
            ruta = Path(f["pdf_path"] or "")
            if not f["pdf_path"] or not ruta.exists():
                res["sin_pdf"] += 1
                continue
            try:
                datos = datos_de_archivo(ruta, proveedor=f["proveedor"] or "edenorte")
            except Exception as exc:  # noqa: BLE001 - PDF ilegible
                res["sin_datos"] += 1
                res["detalle"].append({"nic": f["nic"], "ref": f["ref_pago"],
                                       "error": f"PDF ilegible: {exc}"[:120]})
                continue
            if not datos.corte:
                res["sin_datos"] += 1
                continue
            if not simular:
                conn.execute(
                    "UPDATE facturas SET corte = ?, periodo_ini = ?, periodo_fin = ?, "
                    "dias = ?, emision = COALESCE(emision, ?), vence = COALESCE(vence, ?) "
                    "WHERE id = ?",
                    (datos.corte, datos.periodo_ini, datos.periodo_fin, datos.dias,
                     datos.emision, datos.vence, f["id"]))
                conn.commit()
            res["corregidas"] += 1
            res["detalle"].append({"nic": f["nic"], "ref": f["ref_pago"], "corte": datos.corte})

        # aprendidos los cortes, se completa el dia de corte de los contratos
        pendientes = conn.execute(
            """SELECT f.nic, MAX(f.corte) AS corte FROM facturas f
               JOIN contratos c ON c.nic = f.nic
               WHERE (c.dia_corte IS NULL OR c.dia_corte = 0)
                 AND f.corte IS NOT NULL AND f.estado = 'descargada'
               GROUP BY f.nic""").fetchall()
        for fila in pendientes:
            dia = int(fila["corte"][-2:])
            if not simular:
                db.upsert_contrato(conn, fila["nic"], dia_corte=dia)
            res["contratos_actualizados"] += 1
            res["detalle"].append({"nic": fila["nic"], "dia_corte": dia})

    return res


def main() -> None:
    ap = argparse.ArgumentParser(description="Completa los datos que falten en facturas guardadas")
    ap.add_argument("--simular", action="store_true", help="no escribe, solo informa")
    ap.add_argument("--nic", help="limita a un cliente")
    args = ap.parse_args()

    r = reparar(simular=args.simular, nic=(args.nic or "").strip() or None)
    print(f"revisadas: {r['revisadas']} | corregidas: {r['corregidas']} | "
          f"sin PDF: {r['sin_pdf']} | sin datos: {r['sin_datos']} | "
          f"contratos con dia de corte: {r['contratos_actualizados']}")
    for d in r["detalle"]:
        print("  ", d)
    sys.exit(0)


if __name__ == "__main__":
    main()
