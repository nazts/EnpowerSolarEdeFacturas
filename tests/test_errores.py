"""Errores vistos desde el navegador salen como página, nunca como JSON crudo.

Cuando el PDF de una factura no está en el servidor ni en Telegram, el enlace
abría `/facturas/{id}/pdf` en otra pestaña y el navegador mostraba
`{"detail": "PDF no encontrado"}`. Ahora: página de error con botón de vuelta,
la ficha marca "sin PDF" cuando no se puede abrir, y las rutas /api siguen
respondiendo JSON (las usa el JS).
"""
import asyncio

from fastapi import HTTPException
from starlette.requests import Request

from app import config, dashboard


def _peticion(path: str, acepta: str = "text/html,application/xhtml+xml,*/*;q=0.8") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"accept", acepta.encode())],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8000),
        "app": dashboard.app,
        "router": dashboard.app.router,
    }
    return Request(scope)


def _respuesta(path: str, acepta: str = "text/html,application/xhtml+xml,*/*;q=0.8",
               detalle: str = "PDF no encontrado", codigo: int = 404):
    return asyncio.run(dashboard._error_http(
        _peticion(path, acepta), HTTPException(status_code=codigo, detail=detalle)))


def test_en_el_navegador_sale_la_pagina_de_error():
    resp = _respuesta("/facturas/12/pdf")
    assert resp.status_code == 404
    assert resp.template.name == "error.html"


def test_el_mensaje_llega_a_la_pagina_y_ofrece_volver():
    from starlette.templating import _TemplateResponse  # noqa: F401  (tipo real)

    resp = _respuesta("/clientes/6997885", detalle="El PDF de la factura no está disponible")
    assert resp.context["mensaje"] == "El PDF de la factura no está disponible"
    assert resp.context["volver"] == "/clientes/6997885"
    assert resp.context["texto_volver"] == "Volver a la ficha"
    assert resp.context["codigo"] == 404


def test_las_rutas_api_siguen_devolviendo_json():
    resp = _respuesta("/api/contrato/123", detalle="NIC no reconocido")
    assert resp.status_code == 404
    assert resp.media_type == "application/json"
    assert b"NIC no reconocido" in resp.body


def test_quien_pide_json_recibe_json():
    resp = _respuesta("/facturas/12/pdf", acepta="application/json")
    assert resp.media_type == "application/json"


def test_volver_por_defecto_es_el_listado():
    resp = _respuesta("/facturas/12/pdf")
    assert resp.context["volver"] == "/clientes"


# --------------------------------------------------- disponibilidad del PDF
def test_pdf_disponible_si_tiene_file_id_de_telegram():
    assert dashboard._pdf_disponible({"tg_file_id": "BQACAgEAA", "pdf_path": ""})


def test_pdf_disponible_si_esta_en_disco(tmp_path):
    archivo = tmp_path / "CLIENTE 6997885.pdf"
    archivo.write_bytes(b"%PDF-1.4")
    assert dashboard._pdf_disponible({"tg_file_id": "", "pdf_path": str(archivo)})


def test_pdf_disponible_si_esta_en_la_carpeta_del_proyecto(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FACTURAS_DIR", tmp_path)
    (tmp_path / "CLIENTE 6997885.pdf").write_bytes(b"%PDF-1.4")
    # ruta guardada de la otra maquina, pero el archivo esta aca
    assert dashboard._pdf_disponible(
        {"tg_file_id": "", "pdf_path": r"C:\Users\otro\facturas\CLIENTE 6997885.pdf"})


def test_sin_pdf_ni_file_id_no_esta_disponible(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FACTURAS_DIR", tmp_path)
    assert not dashboard._pdf_disponible({"tg_file_id": "", "pdf_path": ""})
    assert not dashboard._pdf_disponible(
        {"tg_file_id": "", "pdf_path": r"C:\Users\otro\facturas\NO EXISTE.pdf"})


# ------------------------------------------- actividad en vivo (/api/eventos)
def test_api_eventos_devuelve_los_ultimos_y_su_id(db_path):
    from app import db

    with db.sesion(db_path=db_path) as c:
        for i in range(5):
            db.log_evento(c, "info", f"evento {i}", f"detalle {i}")

    datos = dashboard.api_eventos().body
    import json
    carga = json.loads(datos)
    assert len(carga["eventos"]) == 5
    assert [e["mensaje"] for e in carga["eventos"]][0] == "evento 4"     # mas nuevo primero
    assert carga["ultimo_id"] == carga["eventos"][0]["id"]
    assert {"id", "fecha", "usuario", "nivel", "mensaje", "detalle"} <= set(carga["eventos"][0])


def test_api_eventos_solo_devuelve_lo_nuevo(db_path):
    import json

    from app import db

    with db.sesion(db_path=db_path) as c:
        db.log_evento(c, "info", "viejo", "")
        primero = c.execute("SELECT MAX(id) AS i FROM eventos").fetchone()["i"]
        db.log_evento(c, "aviso", "nuevo", "")

    carga = json.loads(dashboard.api_eventos(desde=int(primero)).body)
    assert [e["mensaje"] for e in carga["eventos"]] == ["nuevo"]
    assert carga["ultimo_id"] > primero


def test_api_eventos_respeta_el_limite(db_path):
    import json

    from app import db

    with db.sesion(db_path=db_path) as c:
        for i in range(9):
            db.log_evento(c, "info", f"e{i}", "")

    carga = json.loads(dashboard.api_eventos(limite=3).body)
    assert len(carga["eventos"]) == 3
