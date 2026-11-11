"""Extraccion de datos de la factura en PDF (texto, sin modificar el archivo)."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

_POSIBLES = [
    "pdftotext",
    r"C:\Program Files\Git\mingw64\bin\pdftotext.exe",
    r"C:\Program Files\Git\usr\bin\pdftotext.exe",
]


def _binario() -> str:
    for candidato in _POSIBLES:
        ruta = shutil.which(candidato) or (candidato if Path(candidato).exists() else None)
        if ruta:
            return ruta
    raise RuntimeError("pdftotext no encontrado (instalar poppler / viene con Git for Windows)")


def texto_pdf(path: str | Path) -> str:
    salida = subprocess.run(
        [_binario(), "-layout", str(path), "-"],
        capture_output=True, check=True,
    )
    return salida.stdout.decode("latin-1", errors="replace")


def _num(valor: str | None) -> float | None:
    if not valor:
        return None
    limpio = valor.replace(" ", "").replace(",", "")
    try:
        return float(limpio)
    except ValueError:
        return None


def _fecha(valor: str | None) -> str | None:
    if not valor:
        return None
    try:
        return datetime.strptime(valor.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


@dataclass
class DatosFactura:
    nic: str | None = None
    no_factura: str | None = None
    titular: str | None = None
    periodo_ini: str | None = None
    periodo_fin: str | None = None
    corte: str | None = None
    dias: int | None = None
    emision: str | None = None
    vence: str | None = None
    kwh: float | None = None
    sin_subsidio: float | None = None
    subsidio: float | None = None
    facturado: float | None = None
    tarifa: str | None = None
    suministro: str | None = None
    medidor: str | None = None
    referencia: str | None = None   # Edesur: referencia de pago completa (NIC+3dig-XX)

    def como_dict(self) -> dict:
        return asdict(self)


def parsear(texto: str) -> DatosFactura:
    plano = re.sub(r"[ \t]+", " ", texto)
    d = DatosFactura()

    m = re.search(r"No\. Factura\.*:?\s*(\d{6,})", plano)
    if m:
        d.no_factura = m.group(1)

    m = re.search(r"NOMBRE O RAZON SOCIAL:\s*(.+)", plano)
    if m:
        d.titular = re.sub(r"\s+", " ", m.group(1)).strip()

    m = re.search(r"No\. De D[ií]as Facturados\s*:\s*(\d{2}/\d{2}/\d{4})\s*-\s*"
                  r"(\d{2}/\d{2}/\d{4})\s*=\s*(\d+)\s*d", plano)
    if m:
        d.periodo_ini = _fecha(m.group(1))
        d.periodo_fin = _fecha(m.group(2))
        d.corte = _fecha(m.group(2))
        d.dias = int(m.group(3))

    m = re.search(r"FECHA EMISION\.*:?\s*(\d{2}/\d{2}/\d{4})", plano)
    if m:
        d.emision = _fecha(m.group(1))

    # Vencimiento: la fecha que sigue al rotulo PAGUE ANTES DE
    pos = plano.find("PAGUE ANTES DE")
    if pos >= 0:
        m = re.search(r"(\d{2}/\d{2}/\d{4})", plano[pos:pos + 400])
        if m:
            d.vence = _fecha(m.group(1))
    if not d.vence:
        m = re.search(r"(\d{2}/\d{2}/\d{4})\s*$", plano.strip())
        if m:
            d.vence = _fecha(m.group(1))

    m = re.search(r"([\d][\d\.,]*)\s*kWh", plano)
    if m:
        d.kwh = _num(m.group(1))

    m = re.search(r"IMPORTE SIN SUBSIDIO EN RD\$\s*RD\$\s*(-?[\d\.,]+)", plano)
    if m:
        d.sin_subsidio = _num(m.group(1))
    m = re.search(r"IMPORTE SUBSIDIADO EN RD\$\s*RD\$\s*(-?[\d\.,]+)", plano)
    if m:
        d.subsidio = _num(m.group(1))

    m = re.search(r"FACTURADO MES\s+[A-Z]{3,}[\s\S]{0,200}?RD\$\s*(-?[\d\.,]+)", plano)
    if m:
        d.facturado = _num(m.group(1))
    if d.facturado is None:
        # el codigo de barras repite el total: *<ref>00000000000<total>*
        m = re.search(r"\*(\d{10})\d{11}([\d]+\.\d{2})\*", plano)
        if m:
            d.facturado = _num(m.group(2))

    m = re.search(r"TARIFA\.*:?\s*([A-Z0-9\-]+)", plano)
    if m:
        d.tarifa = m.group(1)
    m = re.search(r"SUMINISTRO No\.*:?\s*(\d+)", plano)
    if m:
        d.suministro = m.group(1)
    m = re.search(r"Medidor\.*:\s*(\d+)", plano)
    if m:
        d.medidor = m.group(1)

    return d


def parsear_edesur(texto: str) -> DatosFactura:
    """Factura de Edesur: `REFERENCIADE PAGO`, `PERIODO DE FACTURACIÓN`, otro layout."""
    plano = re.sub(r"[ \t]+", " ", texto)
    d = DatosFactura()

    m = re.search(r"e-NCF:\s*([A-Z0-9]+)", plano)
    if m:
        d.no_factura = m.group(1)

    # Titular: linea "APELLIDOS, NOMBRES<doc>  Seccion: ..."
    m = re.search(r"^\s*([A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ0-9 .,&\-]{4,}?,\s*[A-ZÁÉÍÓÚÑÜ .,&\-]{3,}?)\s*\d{6,}\s+Secci",
                  plano, re.M)
    if m:
        d.titular = re.sub(r"\s+", " ", m.group(1)).strip()
    if not d.titular:
        m = re.search(r"TITULAR DEL CONTRATO\s*\n\s*([A-ZÁÉÍÓÚÑÜ][^\n]{5,})", plano)
        if m:
            d.titular = re.sub(r"\s+", " ", m.group(1)).strip()

    # NIC / NIS / medidor
    m = re.search(r"NIC:\s*(\d{5,})", plano)
    if m:
        d.nic = m.group(1)
    m = re.search(r"NIS:\s*(\d{5,})", plano)
    if m:
        d.suministro = m.group(1)
    m = re.search(r"Medidor:\s*(\d+)", plano)
    if m:
        d.medidor = m.group(1)

    # Referencia de pago: "5786394193-83" -> se guarda completa
    m = re.search(r"REFERENCIADE PAGO:\s*(\d+-\d+)", plano)
    if m:
        d.referencia = m.group(1)

    # Periodo de facturacion: "12/09/2026 - 13/10/2026 = 31 dias" (fin = corte)
    m = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})\s*=\s*(\d+)\s*d[ií]as", plano)
    if m:
        d.periodo_ini = _fecha(m.group(1))
        d.periodo_fin = _fecha(m.group(2))
        d.corte = _fecha(m.group(2))
        d.dias = int(m.group(3))

    m = re.search(r"FECHADE EMISI[ÓO]N:\s*(\d{2}/\d{2}/\d{4})", plano)
    if m:
        d.emision = _fecha(m.group(1))
    m = re.search(r"FECHAL[ÍI]MITE DE PAGO:\s*(\d{2}/\d{2}/\d{4})", plano)
    if m:
        d.vence = _fecha(m.group(1))

    m = re.search(r"([\d][\d.,]*)\s*kWh", plano)
    if m:
        d.kwh = _num(m.group(1))

    m = re.search(r"IMPORTE TOTAL SIN SUBSIDIO RD\$\s*([\d.,]+)", plano)
    if m:
        d.sin_subsidio = _num(m.group(1))
    m = re.search(r"APORTE TOTAL GOBIERNO RD\$\s*([\d.,]+)", plano)
    if m:
        d.subsidio = _num(m.group(1))
    m = re.search(r"IMPORTE EN RD\$\s*([\d.,]+)", plano) or \
        re.search(r"IMPORTE TOTAL RD\$\s*([\d.,]+)", plano)
    if m:
        d.facturado = _num(m.group(1))

    m = re.search(r"TARIFA:\s*([A-Z0-9]+)", plano)
    if m:
        d.tarifa = m.group(1)

    return d


def detectar_proveedor(texto: str) -> str:
    """`edesur` | `edenorte` segun el texto del PDF."""
    bajo = texto.lower()
    if "edesur" in bajo or "referenciade pago" in bajo:
        return "edesur"
    return "edenorte"


def datos_de_archivo(path: str | Path, proveedor: str = "edenorte") -> DatosFactura:
    texto = texto_pdf(path)
    if proveedor == "auto":
        proveedor = detectar_proveedor(texto)
    if proveedor == "edesur":
        return parsear_edesur(texto)
    return parsear(texto)
