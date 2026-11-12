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
    """El escaneo del dia no toca a quien no corta (el historico es aparte)."""
    p = portal()
    r = runner.corrida(date(2026, 9, 10), enviar=False, cliente=p, historico=False)
    p.historial.assert_not_called()
    assert r.descargadas == [] and pend(contrato) == []


def test_sin_cortes_igual_corre_el_historico(contrato, leer_pdf):
    """Sin cortes hoy la corrida igual mantiene el historico de cada cliente."""
    p = portal(filas=[], pdfs={})
    r = runner.corrida(date(2026, 9, 10), enviar=False, cliente=p)
    p.historial.assert_called_once()
    assert r.historico is not None


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

    # el historico vuelve a consultar el historial: lo importante es que cada
    # NIC se consulte solo en el portal de su distribuidora
    assert set(consultas["edenorte"]) == {NIC}
    assert set(consultas["edesur"]) == {NIC_EDE}
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


# ----------------------------------------------------------------- historico
def _texto_factura(corte, kwh=100, total=1000.0, ref="6997885044"):
    return f"""
No. Factura: {ref}
NOMBRE O RAZON SOCIAL: DE LA CRUZ TAVAREZ, JORGE RAMON
No. De Días Facturados : 16/08/2026 - {corte} = 30 d
FECHA EMISION: {corte}
PAGUE ANTES DE
05/10/2026
Activa B.T. 100 200 1.0000 {kwh} kWh
IMPORTE SIN SUBSIDIO EN RD$ RD$ {total + 100:.2f}
IMPORTE SUBSIDIADO EN RD$ RD$ -{100:.2f}
FACTURADO MES OCT RD$ {total:.2f}
TARIFA: BTD
SUMINISTRO No.: 4237690
Medidor: 06042264
"""


def test_historial_guarda_las_anteriores_y_las_envia_una_vez(contrato, leer_pdf, monkeypatch):
    mensajes = []
    monkeypatch.setattr(telegram, "enviar_documento",
                        lambda path, caption="", chat_id=None: mensajes.append((path, caption)) or {})

    filas = [FacturaRef(nic=NIC, ref_pago=f, cliente="", tipo="Recibos de consumos",
                        emision="16/09/2026") for f in ("111", "222", "333")]
    pdfs = {"111": _texto_factura("16/09/2026").encode("latin-1"),
            "222": _texto_factura("16/08/2026").encode("latin-1"),
            "333": _texto_factura("16/07/2026").encode("latin-1")}
    p = portal(filas=filas, pdfs=pdfs)

    # la del corte de hoy ya esta guardada
    with db.sesion(db_path=contrato) as c:
        db.guardar_factura(c, {"nic": NIC, "ref_pago": "111", "tipo": "Recibos de consumos",
                               "corte": "2026-09-16", "facturado": 1000.0,
                               "estado": "descargada", "enviada": 1})

    res = runner.sincronizar_historial(date(2026, 9, 20), enviar=True,
                                       portales={"edenorte": p}, chat_id="-100")

    # guarda las dos anteriores...
    assert {n["corte"] for n in res["nuevos"]} == {"2026-08-16", "2026-07-16"}
    # ...pero a Telegram solo va una: la ultima que no se habia enviado
    assert res["enviados"] == 1 and res["albumes"] == 1
    assert len(mensajes) == 1                        # un mensaje, una factura
    assert "NIC 6997885" in mensajes[0][1]           # con su propio pie
    with db.sesion(db_path=contrato) as c:
        guardadas = [dict(r) for r in c.execute(
            "SELECT ref_pago, corte, enviada FROM facturas ORDER BY corte")]
    assert [g["corte"] for g in guardadas] == ["2026-07-16", "2026-08-16", "2026-09-16"]
    # la de julio queda guardada pero NO se manda (solo la ultima)
    assert {g["corte"]: g["enviada"] for g in guardadas} == {
        "2026-07-16": 0, "2026-08-16": 1, "2026-09-16": 1}

    # segunda pasada: no descarga ni reenvia nada
    mensajes.clear()
    res2 = runner.sincronizar_historial(date(2026, 9, 21), enviar=True,
                                        portales={"edenorte": p}, chat_id="-100")
    assert res2["nuevos"] == [] and res2["enviados"] == 0 and mensajes == []


def test_historial_respeta_por_cliente(contrato, leer_pdf, monkeypatch):
    monkeypatch.setattr(telegram, "enviar_documento",
                        lambda path, caption="", chat_id=None: {})
    filas = [FacturaRef(nic=NIC, ref_pago=str(i), cliente="", tipo="Recibos de consumos",
                        emision="16/09/2026") for i in range(1, 6)]
    pdfs = {str(i): _texto_factura(f"1{i}/09/2026").encode("latin-1") for i in range(1, 6)}
    p = portal(filas=filas, pdfs=pdfs)

    res = runner.sincronizar_historial(date(2026, 9, 20), por_cliente=2, enviar=False,
                                       portales={"edenorte": p})
    assert len(res["nuevos"]) == 2          # solo las dos mas recientes


def test_historial_aprende_el_dia_de_corte(contrato, leer_pdf, monkeypatch):
    monkeypatch.setattr(telegram, "enviar_documento",
                        lambda path, caption="", chat_id=None: {})
    with db.sesion(db_path=contrato) as c:
        c.execute("UPDATE contratos SET dia_corte = NULL WHERE nic = ?", (NIC,))
        c.commit()
    p = portal(filas=[fila(ref="777")],
               pdfs={"777": _texto_factura("16/09/2026").encode("latin-1")})

    runner.sincronizar_historial(date(2026, 9, 20), enviar=False,
                                 portales={"edenorte": p})

    with db.sesion(db_path=contrato) as c:
        assert db.obtener_contrato(c, NIC)["dia_corte"] == 16


def test_pdf_que_el_portal_ya_no_tiene_no_es_error(contrato, leer_pdf):
    """Sin PDF se salta esa factura y sigue; si fallan 3 seguidas se detiene."""
    from app.edenorte import PdfNoDisponible

    refs = ["900", "899", "898", "897"]
    p = portal(filas=[FacturaRef(nic=NIC, ref_pago=r, cliente="", tipo="Recibos de consumos",
                                 emision="16/09/2026") for r in refs])
    p.descargar_pdf.side_effect = lambda ref: (
        _texto_factura("16/09/2026").encode("latin-1") if ref in ("900", "899")
        else (_ for _ in ()).throw(PdfNoDisponible(f"PDF no disponible para {ref}")))

    res = runner.sincronizar_historial(date(2026, 9, 20), por_cliente=4, enviar=False,
                                       portales={"edenorte": p})

    assert [n["corte"] for n in res["nuevos"]] == ["2026-09-16", "2026-09-16"]
    assert res["errores"] == []          # ni un error: el portal simplemente no los tiene
    assert p.descargar_pdf.call_count == 4   # 900, 899 y las dos sin PDF


def test_factura_nueva_sin_pdf_no_tapa_las_anteriores(contrato, leer_pdf):
    """El caso real: la factura recien cortada esta publicada pero SIN pdf todavia.

    Antes el historico cortaba ahi y el cliente quedaba con 0 facturas.
    """
    from app.edenorte import PdfNoDisponible

    refs = ["900", "899", "898"]
    p = portal(filas=[FacturaRef(nic=NIC, ref_pago=r, cliente="", tipo="Recibos de consumos",
                                 emision="16/09/2026") for r in refs])
    p.descargar_pdf.side_effect = lambda ref: (
        (_ for _ in ()).throw(PdfNoDisponible(f"PDF no disponible para {ref}"))
        if ref == "900"
        else _texto_factura(f"{15 - int(ref[-1])}/09/2026").encode("latin-1"))

    res = runner.sincronizar_historial(date(2026, 9, 20), por_cliente=3, enviar=False,
                                       portales={"edenorte": p})

    assert [n["nic"] for n in res["nuevos"]] == [NIC, NIC]     # las dos viejas si
    assert p.descargar_pdf.call_count == 3                     # incluida la sin PDF
    assert res["errores"] == []
    with db.sesion(db_path=contrato) as c:
        n = c.execute("SELECT COUNT(*) AS n FROM facturas WHERE nic = ?", (NIC,)).fetchone()["n"]
    assert n == 2          # quedaron guardadas en la base del panel


def test_upsert_no_reactiva_un_cliente_desactivado(contrato):
    with db.sesion(db_path=contrato) as c:
        db.upsert_contrato(c, NIC, activo=0)
        db.upsert_contrato(c, NIC, asesor="Steven")      # sin pasar activo
        assert db.obtener_contrato(c, NIC)["activo"] == 0
        db.upsert_contrato(c, NIC, activo=1)
        assert db.obtener_contrato(c, NIC)["activo"] == 1


# ------------------------------------------------- arreglos del QA estricto
def test_guardar_factura_conserva_id_y_enviada(contrato):
    """Reguardar la misma factura no la devuelve a 'sin enviar' ni le cambia el id."""
    with db.sesion(db_path=contrato) as c:
        primero = db.guardar_factura(c, {"nic": NIC, "ref_pago": REF, "tipo": "Recibos de consumos",
                                         "corte": "2026-09-16", "facturado": 100.0,
                                         "estado": "descargada"})
        db.marcar_enviadas(c, [primero], "-100")
        segundo = db.guardar_factura(c, {"nic": NIC, "ref_pago": REF, "tipo": "Recibos de consumos",
                                         "corte": "2026-09-16", "facturado": 100.0,
                                         "estado": "descargada"})
        fila = c.execute("SELECT id, enviada FROM facturas WHERE ref_pago = ?", (REF,)).fetchone()
    assert segundo == primero and fila["id"] == primero
    assert fila["enviada"] == 1


def test_caption_escapa_html_y_formatea_monto():
    texto = runner._caption("2026-09-16", [{
        "nic": "1234567", "razon_social": "A & B <SRL>", "alias": "", "asesor": "",
        "corte": "2026-09-16", "vence": "2026-10-05", "facturado": 1234.5,
    }], [])
    assert "&amp;" in texto and "&lt;SRL&gt;" in texto     # Telegram HTML
    assert "Pagar: RD$1,234.50" in texto


def test_caption_sin_monto_ni_asesor():
    texto = runner._caption("2026-09-16", [{
        "nic": "1234567", "razon_social": "X", "alias": "", "asesor": "",
        "corte": "2026-09-16", "vence": None, "facturado": None,
    }], [])
    assert "s/d" in texto and "Asesor" not in texto and "Vence" not in texto


def test_dos_corridas_a_la_vez_no_se_pisan(contrato, leer_pdf):
    """Mientras otra corrida tiene el lock (otro hilo/proceso), esta no hace nada."""
    import threading
    import time

    p = portal(filas=[fila()], pdfs={REF: _texto_factura("16/09/2026").encode("latin-1")})
    tomado = threading.Event()

    def otra_corrida():
        with runner._exclusivo() as libre:
            assert libre is True
            tomado.set()
            time.sleep(0.4)

    hilo = threading.Thread(target=otra_corrida)
    hilo.start()
    assert tomado.wait(2)
    r = runner.corrida(D0, enviar=False, cliente=p, historico=False)
    hilo.join()

    assert r.descargadas == []
    assert r.historico.get("ocupado") is True


# --------------------------------------------- edición de la ficha del cliente
def test_actualizar_contrato_cambia_y_vacia_campos(contrato):
    with db.sesion(db_path=contrato) as c:
        db.upsert_contrato(c, NIC, razon_social="DE LA CRUZ TAVAREZ, JORGE RAMON",
                           alias="TALLER", asesor="Asesor Uno", notas="algo", dia_corte=16,
                           direccion="Calle 1", paneles="22 JINKO 590W", potencia_paneles=12.98)
        assert db.actualizar_contrato(c, NIC, {
            "alias": "OTRO PAGADOR", "asesor": "", "notas": "",
            "dia_corte": 7, "paneles": "", "potencia_paneles": 9.5,
            "activo": 0, "proveedor": "edesur",
        }) is True
        fila = db.obtener_contrato(c, NIC)
    assert fila["alias"] == "OTRO PAGADOR"
    assert fila["asesor"] == "" and fila["notas"] == "" and fila["paneles"] == ""
    assert fila["dia_corte"] == 7 and fila["activo"] == 0
    assert fila["proveedor"] == "edesur"
    assert fila["potencia_paneles"] == 9.5
    assert fila["direccion"] == "Calle 1"          # lo que no se envía no se toca
    assert fila["razon_social"] == "DE LA CRUZ TAVAREZ, JORGE RAMON"


def test_actualizar_contrato_recalcula_el_nombre_normalizado(contrato):
    with db.sesion(db_path=contrato) as c:
        db.upsert_contrato(c, NIC, razon_social="VIEJO, NOMBRE")
        db.actualizar_contrato(c, NIC, {"razon_social": "GARCIA CASTRO, FRANCISCO ALBERT"})
        fila = db.obtener_contrato(c, NIC)
    assert fila["nombre_normalizado"] == "FRANCISCO ALBERT GARCIA CASTRO"


def test_actualizar_contrato_ignora_columnas_desconocidas(contrato):
    with db.sesion(db_path=contrato) as c:
        db.upsert_contrato(c, NIC, razon_social="X")
        assert db.actualizar_contrato(c, NIC, {"nic": "999", "inventada": "borrame"}) is False
        assert db.obtener_contrato(c, NIC)["nic"] == NIC


# ------------------------------------------------- bloqueo entre corridas (Linux)
def test_el_bloqueo_existe_en_esta_plataforma():
    from app import runner

    assert callable(runner._tomar_bloqueo)
    assert callable(runner._soltar_bloqueo)


def test_no_usa_nombres_que_solo_existen_en_windows():
    """En WSL `msvcrt` no existe: consultarlo con hasattr() daba NameError."""
    from pathlib import Path

    from app import runner

    fuente = Path(runner.__file__).read_text(encoding="utf-8")
    assert "hasattr(msvcrt" not in fuente
    assert "msvcrt.locking" not in fuente or "sys.platform" in fuente


def test_si_otro_proceso_tiene_el_bloqueo_no_se_rompe(db_path, monkeypatch):
    from app import runner

    def ocupado(archivo):
        raise OSError("lo tiene otro proceso")

    monkeypatch.setattr(runner, "_tomar_bloqueo", ocupado)
    with runner._exclusivo() as tomado:
        assert tomado is False


def test_la_rama_de_linux_del_bloqueo_es_valida(monkeypatch):
    """Ejecuta el bloque de bloqueo con sys.platform='linux' y un fcntl falso.

    Asi se valida la rama que en Windows nunca se ejecuta (el bug del NameError
    solo aparecia al correr en WSL).
    """
    import sys
    import types
    from pathlib import Path

    from app import runner

    fuente = Path(runner.__file__).read_text(encoding="utf-8")
    inicio = fuente.index("# Bloqueo de archivo")
    fin = fuente.index("from . import config")
    bloque = fuente[inicio:fin]

    falso = types.ModuleType("fcntl")
    falso.LOCK_EX, falso.LOCK_NB, falso.LOCK_UN = 2, 4, 8
    llamadas = []
    falso.flock = lambda archivo, modo: llamadas.append(modo)
    monkeypatch.setitem(sys.modules, "fcntl", falso)

    class SysFalso:
        platform = "linux"

    espacio: dict = {"sys": SysFalso}
    exec(compile(bloque, "<bloqueo>", "exec"), espacio)

    assert callable(espacio["_tomar_bloqueo"])
    assert callable(espacio["_soltar_bloqueo"])
    espacio["_tomar_bloqueo"](object())
    espacio["_soltar_bloqueo"](object())
    assert llamadas == [2 | 4, 8]


def test_historial_guarda_todas_las_facturas_por_defecto(contrato, leer_pdf, monkeypatch):
    """Sin limite: baja todas las de tipo recibo de consumo que tenga el portal."""
    enviados = []
    monkeypatch.setattr(telegram, "enviar_documento",
                        lambda path, caption="", chat_id=None: enviados.append(path) or {})

    refs = ["901", "902", "903", "904", "905", "906"]
    filas = [FacturaRef(nic=NIC, ref_pago=r, cliente="", tipo="Recibos de consumos",
                        emision="16/09/2026") for r in refs]
    # cada una con su corte, de la mas nueva a la mas vieja
    pdfs = {r: _texto_factura(f"16/{8 - i:02d}/2026").encode("latin-1")
            for i, r in enumerate(refs)}
    p = portal(filas=filas, pdfs=pdfs)

    res = runner.sincronizar_historial(date(2026, 9, 20), enviar=False,
                                       portales={"edenorte": p})

    assert len(res["nuevos"]) == 6          # todas, no tres
    with db.sesion(db_path=contrato) as c:
        n = c.execute("SELECT COUNT(*) AS n FROM facturas WHERE nic = ?",
                      (NIC,)).fetchone()["n"]
    assert n == 6


def test_historial_manda_una_sola_por_cliente_aunque_guarde_muchas(contrato, leer_pdf,
                                                                   monkeypatch):
    mensajes = []
    monkeypatch.setattr(telegram, "enviar_documento",
                        lambda path, caption="", chat_id=None: mensajes.append(
                            (str(path), caption)) or {})

    refs = ["911", "912", "913", "914"]
    filas = [FacturaRef(nic=NIC, ref_pago=r, cliente="", tipo="Recibos de consumos",
                        emision="16/09/2026") for r in refs]
    pdfs = {r: _texto_factura(f"16/{8 - i:02d}/2026").encode("latin-1")
            for i, r in enumerate(refs)}
    p = portal(filas=filas, pdfs=pdfs)

    res = runner.sincronizar_historial(date(2026, 9, 20), enviar=True,
                                       portales={"edenorte": p}, chat_id="-100")

    assert len(res["nuevos"]) == 4          # guarda las cuatro
    assert res["enviados"] == 1             # manda una sola
    assert len(mensajes) == 1
    assert "NIC 6997885" in mensajes[0][1]

    # la que se mando es la de corte mas reciente
    with db.sesion(db_path=contrato) as c:
        enviada = c.execute(
            "SELECT corte FROM facturas WHERE nic = ? AND enviada = 1", (NIC,)).fetchall()
    assert [f["corte"] for f in enviada] == ["2026-08-16"]

    # y una segunda pasada no reenvia nada
    mensajes.clear()
    res2 = runner.sincronizar_historial(date(2026, 9, 21), enviar=True,
                                        portales={"edenorte": p}, chat_id="-100")
    assert res2["enviados"] == 0 and mensajes == []


# ------------------------------------------------ cerrar la sesion al terminar
def test_el_cierre_cierra_lo_que_abrio_aunque_falle():
    from app import runner

    class Portal:
        def __init__(self):
            self.cerrado = False

        def cerrar(self):
            self.cerrado = True

    uno, dos = Portal(), Portal()

    @runner._con_cierre_de_sesiones
    def corre():
        runner._anotar_portales({"a": uno, "b": dos})
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        corre()

    assert uno.cerrado and dos.cerrado      # se cerro aunque hubo error
    assert runner._abiertos == []           # y la lista quedo limpia


def test_el_cierre_no_toca_portales_del_llamador():
    from app import runner

    class Portal:
        def __init__(self):
            self.cerrado = False

        def cerrar(self):
            self.cerrado = True

    ajeno = Portal()

    @runner._con_cierre_de_sesiones
    def corre():
        return "ok"

    assert corre() == "ok"
    assert ajeno.cerrado is False


def test_edenorte_cerrar_hace_post_al_logout_con_token():
    from unittest.mock import MagicMock

    from app.edenorte import EdenorteClient

    cliente = EdenorteClient(email="a@b.c", password="x", base_url="https://ejemplo.test")
    cliente.http = MagicMock()
    cliente.http.get.return_value.text = '<meta name="csrf-token" content="TOKEN123">'
    cliente.logged_in = True

    cliente.cerrar()

    destino = cliente.http.post.call_args[0][0]
    assert destino.endswith("/user/logout")
    assert cliente.http.post.call_args[1]["data"] == {"_csrf": "TOKEN123"}
    assert cliente.http.close.called
    assert cliente.logged_in is False


def test_edesur_cerrar_suelta_el_token():
    from unittest.mock import MagicMock

    from app.edesur import EdesurClient

    cliente = EdesurClient(usuario="u", password="p")
    cliente.token = "abc.def"
    cliente.sesion = MagicMock()

    cliente.cerrar()

    assert cliente.token is None
    assert cliente.sesion.close.called
