"""Extraccion de datos de la factura en PDF (texto, sin modificar el archivo)."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

_POSIBLES = [
    "pdftotext",                    # PATH (Linux: /usr/bin, Windows: Git for Windows)
    "/usr/bin/pdftotext",           # WSL / Ubuntu (poppler-utils)
    "/usr/local/bin/pdftotext",
    r"C:\Program Files\Git\mingw64\bin\pdftotext.exe",
    r"C:\Program Files\Git\usr\bin\pdftotext.exe",
]


def _binario() -> str:
    """Ruta de pdftotext: PDFTOTEXT_BIN del .env, el PATH o las rutas conocidas."""
    import os

    elegido = os.environ.get("PDFTOTEXT_BIN", "").strip()
    candidatos = ([elegido] if elegido else []) + _POSIBLES
    for candidato in candidatos:
        ruta = shutil.which(candidato) or (candidato if Path(candidato).exists() else None)
        if ruta:
            return ruta
    raise RuntimeError(
        "pdftotext no encontrado. En Ubuntu/WSL: sudo apt install poppler-utils. "
        "En Windows viene con Git for Windows, o define PDFTOTEXT_BIN en el .env"
    )


def texto_pdf(path: str | Path) -> str:
    """Texto del PDF tal cual (el archivo no se modifica).

    Ojo con el encoding: pdftotext en Linux/WSL escupe UTF-8 y en Windows latin-1.
    Decodificar siempre como latin-1 rompia los acentos ("Días" -> "DÃ­as") y con
    eso se perdian la fecha de corte y el periodo facturado.
    """
    binario = _binario()
    try:
        salida = subprocess.run([binario, "-layout", "-enc", "UTF-8", str(path), "-"],
                                capture_output=True, check=True)
    except subprocess.CalledProcessError:
        # poppler viejo sin -enc: se pide sin la opcion y se adivina el codec
        salida = subprocess.run([binario, "-layout", str(path), "-"],
                                capture_output=True, check=True)
    for codec in ("utf-8", "cp1252", "latin-1"):
        try:
            return salida.stdout.decode(codec)
        except UnicodeDecodeError:
            continue
    return salida.stdout.decode("utf-8", errors="replace")


# una fecha suelta: 5/10/2026, 05-10-26, 05.10.2026
_FECHA = r"\d{1,2}[/\-. ]\d{1,2}[/\-. ]\d{2,4}"
_PAREO = rf"({_FECHA})\s*(?:-|–|—|\bal\b|\ba\b)\s*({_FECHA})"


def _periodo(plano: str) -> tuple[str | None, str | None, int | None]:
    """(inicio, fin, dias) del periodo facturado; el fin es la fecha de corte.

    Tolera: tildes o su mojibake, mayusculas, '-', '–', '—', 'al', fechas de uno o
    dos digitos, año de dos digitos y varios periodos en el mismo PDF (se queda con
    el mas nuevo).
    """
    limpio = _sin_acentos(plano)
    patrones = (
        # Edenorte: "No. De Dias Facturados : 20/09/2026 - 20/10/2026 = 30 dias"
        rf"D\S{{0,4}}as\s+FACTURADOS[\s:.\-]{{0,20}}{_PAREO}(?:\s*=?\s*(\d+)\s*d)?",
        # Edesur: "PERIODO DE FACTURACION" y las fechas mas abajo, a veces con
        # "NIS: 2115589 2.121 kW 15/07/2026 - 13/08/2026 = 29 dias" en el medio
        rf"PERIODO\s+(?:DE\s+)?FACTURAC\w*[\s\S]{{0,200}}?{_PAREO}(?:\s*=?\s*(\d+)\s*d)?",
        # ultimo recurso (Edesur sin la etiqueta): "15/07/2026 - 16/09/2026 = 29 dias"
        rf"{_PAREO}\s*=\s*(\d+)\s*d",
    )
    mejor: tuple[str | None, str | None, int | None] = (None, None, None)
    for patron in patrones:
        for m in re.finditer(patron, limpio, re.I):
            ini, fin = _fecha(m.group(1)), _fecha(m.group(2))
            if not ini or not fin:
                continue
            dias = int(m.group(3)) if m.lastindex and m.lastindex >= 3 and m.group(3) else None
            if mejor[1] is None or fin > mejor[1]:
                mejor = (ini, fin, dias)
        if mejor[1]:
            break
    return mejor


def _sin_acentos(texto: str) -> str:
    """Copia sin tildes, solo para comparar etiquetas (no se usa para guardar)."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", texto)
                   if not unicodedata.combining(c))


def _num(valor: str | None) -> float | None:
    if not valor:
        return None
    limpio = valor.replace(" ", "").replace(",", "")
    try:
        return float(limpio)
    except ValueError:
        return None


def _fecha(valor: str | None) -> str | None:
    """'5/10/2026', '05-10-26' y '05/10/2026' -> '2026-10-05'."""
    if not valor:
        return None
    limpio = valor.strip().replace("-", "/").replace(".", "/")
    for formato in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(limpio, formato).strftime("%Y-%m-%d")
        except ValueError:
            continue
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

    # Periodo facturado: "No. De Dias Facturados : 20/09/2026 - 20/10/2026 = 30 dias"
    # El fin del periodo ES la fecha de corte.
    d.periodo_ini, d.periodo_fin, d.dias = _periodo(plano)
    d.corte = d.periodo_fin

    m = re.search(r"FECHA EMISION\.*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})", plano)
    if m:
        d.emision = _fecha(m.group(1))

    # Vencimiento: la fecha que sigue al rotulo PAGUE ANTES DE
    pos = plano.find("PAGUE ANTES DE")
    if pos >= 0:
        m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", plano[pos:pos + 400])
        if m:
            d.vence = _fecha(m.group(1))
    if not d.vence:
        m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*$", plano.strip())
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

    # red de seguridad: si no aparecio la etiqueta, el corte es el fin del periodo
    if not d.corte and d.periodo_fin:
        d.corte = d.periodo_fin

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
    d.periodo_ini, d.periodo_fin, d.dias = _periodo(plano)
    d.corte = d.periodo_fin

    m = re.search(r"FECHADE EMISI[ÓO]N:\s*(\d{1,2}/\d{1,2}/\d{2,4})", plano)
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
