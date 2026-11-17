"""Modulo Reenviar: mandar otra vez la ultima factura de los clientes elegidos.

Sirve para cuando el envio anterior fallo o el mensaje se borro del grupo: a
diferencia de la corrida, no importa si la factura ya estaba marcada como
enviada — se vuelve a mandar igual.
"""
import pytest

from app import db, runner, telegram

NIC = "6997885"
REF = "6997885045"


def _cliente(nic=NIC, razon="DE LA CRUZ TAVAREZ, JORGE RAMON", alias="TALLER VALLE VERDE"):
    with db.sesion() as c:
        db.upsert_contrato(c, nic, razon_social=razon, alias=alias, asesor="Steven Lora",
                           dia_corte=16)


def _factura(nic=NIC, ref=REF, corte="2026-10-16", enviada=0, pdf=None):
    with db.sesion() as c:
        return db.guardar_factura(c, {
            "nic": nic, "ref_pago": ref, "tipo": "Recibos de consumos", "corte": corte,
            "facturado": 243.31, "estado": "descargada", "enviada": enviada,
            "pdf_path": str(pdf) if pdf else "",
        })


class _PortalFalso:
    def __init__(self, contenido=b"%PDF-1.4 del portal"):
        self.contenido = contenido
        self.pedidos = []

    def login(self):
        return None

    def cerrar(self):
        return None

    def descargar_pdf(self, ref):
        self.pedidos.append(ref)
        return self.contenido


@pytest.fixture
def enviados(monkeypatch):
    registro = []

    def falso(path, caption="", chat_id=None):
        with open(path, "rb") as fh:
            registro.append({"bytes": fh.read(), "caption": caption, "chat": chat_id})
        return {"message_id": 555, "document": {"file_id": "FILE-RE"}}

    monkeypatch.setattr(telegram, "enviar_documento", falso)
    return registro


def test_reenvia_una_factura_que_ya_estaba_enviada(db_path, enviados, tmp_path):
    _cliente()
    pdf = tmp_path / "FACTURA.pdf"
    pdf.write_bytes(b"%PDF-1.4 original")
    _factura(enviada=1, pdf=pdf)

    res = runner.reenviar_facturas([NIC], chat_id="-100", portales={"edenorte": _PortalFalso()})

    assert res["enviados"] == [{"nic": NIC, "corte": "2026-10-16"}]
    assert res["errores"] == []
    assert len(enviados) == 1 and enviados[0]["bytes"].startswith(b"%PDF")
    with db.sesion() as c:
        fila = c.execute("SELECT enviada, tg_file_id FROM facturas WHERE ref_pago = ?",
                         (REF,)).fetchone()
    assert fila["enviada"] == 1 and fila["tg_file_id"] == "FILE-RE"


def test_el_pie_del_reenvio_lleva_los_datos_del_cliente(db_path, enviados, tmp_path):
    _cliente()
    pdf = tmp_path / "F.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    _factura(pdf=pdf)

    runner.reenviar_facturas([NIC], chat_id="-100", portales={"edenorte": _PortalFalso()})

    pie = enviados[0]["caption"]
    assert pie.splitlines()[0] == f"NIC {NIC}"
    assert "Razón social: JORGE RAMON DE LA CRUZ TAVAREZ" in pie
    assert "Referencia o alias: TALLER VALLE VERDE" in pie
    assert "Pagar: RD$243.31" in pie


def test_si_no_hay_copia_la_pide_al_portal_y_no_la_guarda(db_path, enviados, tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "FACTURAS_DIR", tmp_path / "facturas")
    _cliente()
    _factura()                                     # sin pdf_path ni file_id
    portal = _PortalFalso()

    res = runner.reenviar_facturas([NIC], chat_id="-100", portales={"edenorte": portal})

    assert res["enviados"] and portal.pedidos == [REF]
    assert not (tmp_path / "facturas").exists() or not list((tmp_path / "facturas").glob("*.pdf"))


def test_desde_telegram_si_ya_no_esta_en_disco(db_path, enviados, monkeypatch):
    _cliente()
    _factura()
    with db.sesion() as c:
        c.execute("UPDATE facturas SET tg_file_id = 'FILE-VIEJO' WHERE ref_pago = ?", (REF,))
        c.commit()

    monkeypatch.setattr(telegram, "descargar_archivo", lambda fid: b"%PDF-1.4 de telegram")
    portal = _PortalFalso()

    res = runner.reenviar_facturas([NIC], chat_id="-100", portales={"edenorte": portal})

    assert res["enviados"]
    assert portal.pedidos == []                    # no molesta al portal
    assert enviados[0]["bytes"].startswith(b"%PDF")


def test_un_cliente_sin_facturas_da_error_claro(db_path, enviados):
    _cliente()
    res = runner.reenviar_facturas([NIC], chat_id="-100", portales={"edenorte": _PortalFalso()})
    assert res["enviados"] == []
    assert res["errores"][0]["error"] == "no tiene facturas para enviar"
    assert enviados == []


def test_listado_para_el_modulo(db_path, tmp_path):
    _cliente()
    _cliente("7533709", razon="OTRO CLIENTE SA", alias="")
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    _factura(enviada=1, pdf=pdf)

    with db.sesion() as c:
        filas = {f["nic"]: f for f in db.clientes_con_ultima_factura(c)}

    assert set(filas) == {NIC, "7533709"}
    assert filas[NIC]["corte"] == "2026-10-16"
    assert filas[NIC]["enviada"] == 1
    assert filas["7533709"]["factura_id"] is None      # sin facturas: se puede ver, no enviar
