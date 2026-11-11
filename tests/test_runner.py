from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from app import db, runner, telegram
from app.edenorte import FacturaRef, Historial, PortalError

NIC = "6997885"
REF = "6997885044"
CORTE = "2026-09-16"
D0, D1, D2, D3 = date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 19)


# ------------------------------------------------------------- corta_hoy / es_consumo
def test_corta_hoy_dia_exacto():
    assert runner.corta_hoy(16, date(2026, 9, 16)) is True
    assert runner.corta_hoy(16, date(2026, 9, 15)) is False
    assert runner.corta_hoy(16, date(2026, 9, 17)) is False


@pytest.mark.parametrize("f,esperado", [
    (date(2026, 2, 28), True), (date(2026, 2, 27), False),
    (date(2028, 2, 29), True), (date(2028, 2, 28), False),
    (date(2026, 4, 30), True), (date(2026, 1, 31), True),
])
def test_corta_hoy_dia_31_resuelve_ultimo_dia(f, esperado):
    assert runner.corta_hoy(31, f) is esperado


@pytest.mark.parametrize("v", [None, 0])
def test_corta_hoy_sin_dia_corte(v):
    assert runner.corta_hoy(v, date(2026, 9, 16)) is False


def test_es_consumo():
    assert runner.es_consumo("Recibos de consumos")
    assert not runner.es_consumo("Recibos de servicios")
    assert not runner.es_consumo("")
    assert not runner.es_consumo(None)


# ------------------------------------------------------------------ portal simulado
def portal(filas=None, pdfs=None):
    p = MagicMock(name="EdenorteClient")
    p.login.return_value = None
    p.contratos.return_value = []
    p.historial.return_value = Historial(nic=NIC, filas=filas or [])
    p.descargar_pdf.side_effect = lambda ref: (pdfs or {})[ref]
    return p


def fila(ref=REF, tipo="Recibos de consumos"):
    return FacturaRef(nic=NIC, ref_pago=ref, cliente="X", tipo=tipo, emision="16/09/2026")


@pytest.fixture
def contrato(db_path):
    with db.sesion(db_path=db_path) as c:
        db.upsert_contrato(c, NIC, razon_social="DE LA CRUZ TAVAREZ, JORGE RAMON", dia_corte=16)
    return db_path


def pend(db_path):
    with db.sesion(db_path=db_path) as c:
        return [dict(r) for r in c.execute("SELECT * FROM pendientes")]


def test_ciclo_pendiente_hasta_atrasado_y_cierre(contrato, leer_pdf, texto, tmp_path):
    p = portal()
    r0 = runner.corrida(D0, enviar=False, cliente=p)
    assert r0.descargadas == [] and r0.portal_error is None
    pe = pend(contrato)
    assert len(pe) == 1 and pe[0]["intentos"] == 1 and pe[0]["estado"] == "pendiente"
    assert pe[0]["fecha_corte"] == CORTE

    runner.corrida(D1, enviar=False, cliente=p)
    pe = pend(contrato)
    assert pe[0]["intentos"] == 2 and pe[0]["estado"] == "pendiente"

    runner.corrida(D2, enviar=False, cliente=p)
    pe = pend(contrato)
    assert pe[0]["intentos"] == 3 and pe[0]["estado"] == "atrasado"

    p.historial.return_value = Historial(nic=NIC, filas=[fila()])
    p.descargar_pdf.side_effect = lambda ref: texto().encode("latin-1")
    r = runner.corrida(D3, enviar=False, cliente=p)
    assert len(r.descargadas) == 1
    destino = Path(r.descargadas[0]["path"])
    assert destino.name == f"JORGE RAMON DE LA CRUZ TAVAREZ {NIC}.pdf"
    assert destino.parent == tmp_path / "facturas" and destino.exists()
    assert REF not in destino.name
    assert pend(contrato)[0]["estado"] == "resuelto"
    with db.sesion(db_path=contrato) as c:
        assert db.factura_existe(c, REF)
        assert c.execute("SELECT estado FROM facturas").fetchone()["estado"] == "descargada"
        assert db.pendientes_abiertos(c) == []


def test_no_descarga_tipo_distinto_de_consumo(contrato, leer_pdf, texto):
    p = portal([fila(tipo="Recibos de servicios")], {REF: texto().encode("latin-1")})
    r = runner.corrida(D0, enviar=False, cliente=p)
    assert r.descargadas == []
    p.descargar_pdf.assert_not_called()
    assert pend(contrato)[0]["intentos"] == 1


def test_no_descarga_si_corte_distinto(contrato, leer_pdf, texto):
    p = portal([fila()], {REF: texto().encode("latin-1")})
    r = runner.corrida(date(2026, 10, 16), enviar=False, cliente=p)
    assert r.descargadas == []
    assert r.descartadas[0]["motivo"] == "corte distinto"


def test_no_descarga_consumo_en_blanco(contrato, leer_pdf, texto, tmp_path):
    pdf = texto(consumo="", detalle_kwh=False).encode("latin-1")
    p = portal([fila()], {REF: pdf})
    r = runner.corrida(D0, enviar=False, cliente=p)
    assert r.descargadas == []
    assert r.descartadas[0]["motivo"].startswith("sin consumo")
    assert not (tmp_path / "facturas").exists() or not list((tmp_path / "facturas").glob("*.pdf"))
    with db.sesion(db_path=contrato) as c:
        assert c.execute("SELECT estado FROM facturas").fetchone()["estado"] == "sin_consumo"


def test_contrato_sin_corte_hoy_no_se_consulta(contrato, leer_pdf):
    p = portal()
    r = runner.corrida(date(2026, 9, 10), enviar=False, cliente=p)
    p.historial.assert_not_called()
    assert r.descargadas == [] and pend(contrato) == []


# ------------------------------------------------------------------ PortalError
def test_login_portal_error_no_tumba_y_registra_alerta(contrato, monkeypatch):
    runner.corrida(D0, enviar=False, cliente=portal())
    antes = pend(contrato)
    assert len(antes) == 1

    enviados = []
    monkeypatch.setattr(telegram, "enviar_mensaje", lambda t, chat_id=None, **k: enviados.append(t))
    p = portal()
    p.login.side_effect = PortalError("502 Bad Gateway")
    r = runner.corrida(D1, enviar=True, cliente=p)

    assert "502" in r.portal_error
    assert r.mensaje_enviado is True
    assert enviados and runner.ALERTA_PORTAL in enviados[0]
    with db.sesion(db_path=contrato) as c:
        ev = [e for e in db.ultimos_eventos(c) if e["mensaje"] == runner.ALERTA_PORTAL]
        assert ev and ev[0]["nivel"] == "error" and "502" in ev[0]["detalle"]
    assert pend(contrato) == antes
    p.historial.assert_not_called()


def test_alerta_con_telegram_caido_no_tumba(contrato, monkeypatch):
    def falla(*a, **k):
        raise telegram.TelegramError("sin red")
    monkeypatch.setattr(telegram, "enviar_mensaje", falla)
    p = portal()
    p.login.side_effect = PortalError("502")
    r = runner.corrida(D1, enviar=True, cliente=p)
    assert r.portal_error and r.mensaje_enviado is False
    with db.sesion(db_path=contrato) as c:
        assert any("No se pudo enviar la alerta" in e["mensaje"] for e in db.ultimos_eventos(c))


def test_historial_502_mantiene_pendiente_e_incrementa(contrato):
    runner.corrida(D0, enviar=False, cliente=portal())
    p = portal()
    p.historial.side_effect = PortalError("502 Bad Gateway")
    runner.corrida(D1, enviar=False, cliente=p)
    pe = pend(contrato)
    assert len(pe) == 1 and pe[0]["intentos"] == 2 and pe[0]["estado"] == "pendiente"
    with db.sesion(db_path=contrato) as c:
        assert any("Historial no disponible" in e["mensaje"] for e in db.ultimos_eventos(c))


def test_envio_telegram_tras_descarga(contrato, leer_pdf, texto, monkeypatch):
    llamadas = []
    monkeypatch.setattr(telegram, "enviar_album",
                        lambda paths, caption="", chat_id=None: llamadas.append(paths) or [{}])
    p = portal([fila()], {REF: texto().encode("latin-1")})
    r = runner.corrida(D0, enviar=True, cliente=p, chat_id="-100")
    assert len(llamadas) == 1 and len(llamadas[0]) == 1
    assert r.mensaje_enviado


# --------------------------------------------------- dos distribuidoras a la vez
def test_cada_contrato_usa_su_distribuidora(contrato, leer_pdf, texto):
    """Un contrato de Edesur no debe consultarse en el portal de Edenorte."""
    from app.edenorte import Contrato, FacturaRef, Historial

    NIC_EDE = "2115589"
    REF_EDE = "2115589102026081300"
    with db.sesion(db_path=contrato) as c:
        db.upsert_contrato(c, NIC_EDE, razon_social="ORTIZ MARTINEZ, WILLIAN MARIA",
                           dia_corte=16, proveedor="edesur")

    texto_edesur = """
 REFERENCIADE PAGO: 2115589328-18 SUMIN.: Itiner.: 0003
 FECHADE EMISIÓN: 18/08/2026 REF.: LDO DER N°42A NIC: 2115589
 ORTIZ MARTINEZ, WILLIAN MARIA20223224 Sección: ZONA URBANA Medidor: 15606198
 FECHALÍMITE DE PAGO: 17/09/2026 Este es su número de contrato
 NIS: 2115589 2.121 kW 15/07/2026 - 16/09/2026 = 29 días
 Activa B.T. 6,553 6,553 1.0000 267 kWh Cargo fijo
 CALLE DUARTE IMPORTE EN RD$ 1,914.12
"""

    consultas = {"edenorte": [], "edesur": []}

    def portal_falso(nombre, nic, ref, contenido):
        p = MagicMock(name=nombre)
        p.login.return_value = None
        p.contratos.return_value = []
        p.asociar_contrato.side_effect = AssertionError("no deberia asociar")
        p.historial.side_effect = lambda n: (consultas[nombre].append(n),
                                             Historial(nic=n, filas=[FacturaRef(
                                                 nic=n, ref_pago=ref, cliente="", tipo="Recibos de consumos",
                                                 emision="16/09/2026")]))[1]
        p.descargar_pdf.side_effect = lambda r: contenido
        return p

    edenorte = portal_falso("edenorte", NIC, REF, texto().encode("latin-1"))
    edesur = portal_falso("edesur", NIC_EDE, REF_EDE, texto_edesur.encode("latin-1"))

    r = runner.corrida(D0, enviar=False, portales={"edenorte": edenorte, "edesur": edesur})

    assert consultas["edenorte"] == [NIC]
    assert consultas["edesur"] == [NIC_EDE]
    assert {d["nic"] for d in r.descargadas} == {NIC, NIC_EDE}
    edesur_ok = next(d for d in r.descargadas if d["nic"] == NIC_EDE)
    assert edesur_ok["facturado"] == pytest.approx(1914.12)
    assert edesur_ok["kwh"] == 267

    with db.sesion(db_path=contrato) as c:
        assert db.obtener_contrato(c, NIC_EDE)["proveedor"] == "edesur"
        assert db.obtener_contrato(c, NIC)["proveedor"] == "edenorte"


def test_si_una_distribuidora_falla_la_otra_sigue(contrato, leer_pdf, texto, monkeypatch):
    from app.edenorte import PortalError

    avisos = []
    monkeypatch.setattr(telegram, "enviar_mensaje",
                        lambda t, chat_id=None, **k: avisos.append(t))

    edenorte = portal(filas=[fila()], pdfs={REF: texto().encode("latin-1")})
    edesur = MagicMock(name="edesur")
    edesur.login.side_effect = PortalError("Edesur no responde")

    r = runner.corrida(D0, enviar=True, portales={"edenorte": edenorte, "edesur": edesur},
                       chat_id="-100")

    assert [d["nic"] for d in r.descargadas] == [NIC]        # Edenorte si trabajo
    assert any("Edesur" in a for a in avisos)                # y se aviso de la falla
