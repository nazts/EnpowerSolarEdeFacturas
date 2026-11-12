"""Fixtures comunes: BD SQLite temporal, carpeta de facturas temporal y texto de factura."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, db  # noqa: E402


def texto_factura(*, titular="DE LA CRUZ TAVAREZ, JORGE RAMON", ref="6997885044",
                  ini="16/08/2026", fin="16/09/2026", dias=31, emision="16/09/2026",
                  vence="16/10/2026", consumo="35kWh", detalle_kwh=True,
                  sin_sub="629.59", sub="-380.31", total="249.28",
                  total_misma_linea=False, codigo_barras=True, no_factura="202610180903"):
    """Texto tipo `pdftotext -layout` de una factura de consumo de Edenorte."""
    l = [
        f"No. Factura..................: {no_factura}",
        f"Ref. Pago......................: {ref}",
        f"TITULAR DE PAGO............: {titular}",
        f"FECHA EMISION..........:{emision}",
        f"NOMBRE O RAZON SOCIAL: {titular}",
        "TARIFA..............: BTS-1",
        "SUMINISTRO No.......: 4392988",
        "Medidor........................: 76650755",
        f"No. De Días Facturados : {ini} - {fin} = {dias} dias",
        f"Activa B.T.  76650755    417    452    1.00    {consumo}    Cargo Fijo",
    ]
    if detalle_kwh:
        l.append("35 kWh X RD$5.97        RD$   208.95")
    l += [
        f"IMPORTE SIN SUBSIDIO EN RD$      RD$    {sin_sub}",
        f"IMPORTE SUBSIDIADO EN RD$        RD$   {sub}",
    ]
    if total_misma_linea:
        l.append(f"FACTURADO MES SEP     RD${total}")
    else:
        l += ["FACTURADO MES SEP", "Sep Oct Nov Dic", f"        RD${total}"]
    l += ["PAGUE ANTES DE", "Mes", f"                {vence}"]
    if codigo_barras:
        l.append(f"*{ref}300000000000{total}*")
    return "\r\n".join(l)


@pytest.fixture(autouse=True)
def sin_credenciales(monkeypatch):
    """Ninguna prueba debe llegar a los portales reales."""
    monkeypatch.setattr(config, "EDENORTE_EMAIL", "")
    monkeypatch.setattr(config, "EDENORTE_PASSWORD", "")
    monkeypatch.setattr(config, "EDESUR_USER", "")
    monkeypatch.setattr(config, "EDESUR_PASSWORD", "")
    monkeypatch.setattr(config, "EDESUR_TOKEN", "")
    # las pruebas usan SQLite temporal, nunca la base de Supabase
    monkeypatch.setattr(config, "DATABASE_URL", "")


@pytest.fixture
def texto():
    return texto_factura


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """BD temporal. config se lee al importar, asi que se parchea y ademas se pasa explicita."""
    ruta = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", ruta)
    monkeypatch.setattr(config, "FACTURAS_DIR", tmp_path / "facturas")
    with db.sesion(db_path=ruta) as conn:
        db.init_db(conn)
    return ruta


@pytest.fixture
def leer_pdf(monkeypatch):
    """runner.datos_de_archivo sin pdftotext: los 'PDF' simulados son el texto en bytes."""
    from app import runner, pdfdata

    def _leer(p, proveedor="edenorte"):
        texto = Path(p).read_bytes().decode("latin-1")
        return pdfdata.parsear_edesur(texto) if proveedor == "edesur" else pdfdata.parsear(texto)

    monkeypatch.setattr(runner, "datos_de_archivo", _leer)
