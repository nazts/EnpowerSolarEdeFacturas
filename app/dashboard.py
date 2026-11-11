"""Dashboard web: alta de NIC, autocompletado de razón social, alias/asesor y Excel."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db, excel, naming, telegram
from .edenorte import EdenorteClient, PortalError

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="Enpower Solar — Facturas Edenorte", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


# ------------------------------------------------------------------ API datos
@app.get("/api/contrato/{nic}")
def api_contrato(nic: str) -> JSONResponse:
    """Autocompleta razón social / dirección consultando el portal."""
    try:
        cliente = EdenorteClient()
        cliente.login()
        for c in cliente.contratos():
            if c.nic == str(nic).strip():
                return JSONResponse({
                    "ok": True, "nic": c.nic, "razon_social": c.titular,
                    "nombre_normalizado": naming.normalizar_razon_social(c.titular),
                    "direccion": c.direccion, "telefono": c.telefono,
                })
        return JSONResponse({"ok": False, "error": "Ese NIC no está asociado a la cuenta "
                                                   "en la Oficina Virtual"}, status_code=404)
    except PortalError as exc:
        return JSONResponse({"ok": False, "error": f"Portal: {exc}"}, status_code=502)


@app.get("/api/resumen")
def api_resumen() -> JSONResponse:
    """Agregados para la vista grafica del panel (tarjetas + graficos)."""
    from .fechas import dias_para_corte, proximo_corte

    with db.sesion() as conn:
        datos = db.resumen_dashboard(conn)
        clientes = db.resumen_clientes(conn)

    proximos = []
    for c in clientes:
        prox = proximo_corte(c.get("dia_corte"))
        if not prox:
            continue
        proximos.append({
            "nic": c["nic"],
            "nombre": c.get("nombre_normalizado") or c.get("razon_social") or c["nic"],
            "alias": c.get("alias") or "",
            "asesor": c.get("asesor") or "",
            "fecha": prox.isoformat(),
            "dias": dias_para_corte(c.get("dia_corte")),
            "ultimo_monto": c.get("ultimo_monto"),
        })
    proximos.sort(key=lambda x: x["fecha"])
    datos["proximos_cortes"] = proximos[:12]
    return JSONResponse(datos)


@app.get("/api/estado")
def api_estado() -> JSONResponse:
    with db.sesion() as conn:
        contratos = [dict(r) for r in db.listar_contratos(conn, solo_activos=False)]
        pendientes = [dict(r) for r in db.pendientes_abiertos(conn)]
        facturas = [dict(r) for r in conn.execute(
            "SELECT * FROM facturas WHERE estado='descargada' ORDER BY corte DESC LIMIT 100")]
        eventos = [dict(r) for r in db.ultimos_eventos(conn, 30)]
    return JSONResponse({
        "contratos": contratos, "pendientes": pendientes,
        "facturas": facturas, "eventos": eventos,
        "telegram_configurado": bool(config.TELEGRAM_CHAT_ID),
    })


@app.post("/api/test-telegram")
def api_test_telegram() -> JSONResponse:
    try:
        telegram.enviar_mensaje("✅ Prueba de conexión del bot Enpower Solar.")
        return JSONResponse({"ok": True})
    except telegram.TelegramError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


# ------------------------------------------------------------------ telegram
@app.get("/telegram")
def telegram_page(request: Request):
    try:
        info = telegram.info_bot()
    except Exception as exc:
        info = {"error": str(exc)}
    try:
        pendientes = telegram.updates()
    except Exception:
        pendientes = []
    canales = []
    for upd in pendientes:
        msg = upd.get("channel_post") or upd.get("message") or upd.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            canales.append({"tipo": chat.get("type"), "id": chat.get("id"),
                            "titulo": chat.get("title") or chat.get("username")})
    with db.sesion() as conn:
        envios = [dict(e) for e in conn.execute(
            "SELECT * FROM envios ORDER BY id DESC LIMIT 20")]
    return templates.TemplateResponse(request, "telegram.html", {
        "activo": "telegram",
        "bot": info, "canales": canales, "envios": envios,
        "chat_id": config.TELEGRAM_CHAT_ID,
        "token_configurado": bool(config.TELEGRAM_BOT_TOKEN),
    })


@app.post("/api/telegram/detectar")
def api_telegram_detectar() -> JSONResponse:
    """Busca el canal/grupo donde está el bot y guarda su chat_id."""
    try:
        chat_id = telegram.resolver_chat_id(config.TELEGRAM_CHAT_ID or None)
    except telegram.TelegramError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    config.guardar_chat_id(chat_id)
    with db.sesion() as conn:
        db.log_evento(conn, "info", "Canal de Telegram detectado", chat_id)
    return JSONResponse({"ok": True, "chat_id": chat_id})


@app.post("/api/telegram/chat")
def api_telegram_chat(chat_id: str = Form(...)) -> JSONResponse:
    chat_id = chat_id.strip()
    if not chat_id:
        return JSONResponse({"ok": False, "error": "chat_id vacío"}, status_code=400)
    config.guardar_chat_id(chat_id)
    return JSONResponse({"ok": True, "chat_id": chat_id})


@app.post("/api/corrida")
def api_corrida(fecha: str | None = Form(default=None)) -> JSONResponse:
    from .runner import corrida
    f = datetime.strptime(fecha, "%Y-%m-%d").date() if fecha else None
    try:
        r = corrida(fecha=f, enviar=True)
        return JSONResponse({"ok": r.portal_error is None, **r.como_dict()})
    except Exception as exc:  # el dashboard nunca debe caerse
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/export/excel")
def exportar_excel():
    ruta = excel.exportar()
    return FileResponse(
        ruta, filename=Path(ruta).name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ clientes
@app.get("/clientes")
def clientes(request: Request):
    from .fechas import dias_para_corte, proximo_corte

    with db.sesion() as conn:
        filas = db.resumen_clientes(conn)
    for f in filas:
        f["proximo_corte"] = proximo_corte(f.get("dia_corte"))
        f["dias_corte"] = dias_para_corte(f.get("dia_corte"))
    return templates.TemplateResponse(request, "clientes.html",
                                      {"clientes": filas, "activo": "clientes"})


@app.get("/clientes/nuevo")
def cliente_nuevo(request: Request):
    return templates.TemplateResponse(request, "cliente_nuevo.html",
                                      {"activo": "cliente_nuevo"})


@app.post("/clientes")
def crear_cliente(nic: str = Form(...), razon_social: str = Form(default=""),
                  alias: str = Form(default=""), asesor: str = Form(default=""),
                  dia_corte: str = Form(default=""), notas: str = Form(default="")):
    nic = nic.strip()
    if not nic.isdigit():
        raise HTTPException(400, "El NIC debe ser numérico")
    with db.sesion() as conn:
        db.upsert_contrato(
            conn, nic, razon_social=razon_social, alias=alias, asesor=asesor,
            notas=notas,
            dia_corte=int(dia_corte) if dia_corte.strip().isdigit() else None,
        )
        db.log_evento(conn, "info", f"Cliente {nic} agregado desde el módulo de clientes")
    return RedirectResponse(f"/clientes/{nic}", status_code=303)


@app.get("/clientes/{nic}")
def cliente_detalle(request: Request, nic: str):
    from .fechas import dias_para_corte, proximo_corte

    with db.sesion() as conn:
        contrato = db.obtener_contrato(conn, nic)
        if contrato is None:
            raise HTTPException(404, "Ese NIC no está registrado")
        historico = [dict(f) for f in db.historico_facturas(conn, nic)]
        pendientes = [dict(p) for p in db.pendientes_abiertos(conn) if str(p["nic"]) == nic]
    datos = dict(contrato)
    datos["proximo_corte"] = proximo_corte(datos.get("dia_corte"))
    datos["dias_corte"] = dias_para_corte(datos.get("dia_corte"))
    total = sum(f["facturado"] or 0 for f in historico)
    return templates.TemplateResponse(request, "cliente_detalle.html", {
        "cliente": datos, "historico": historico, "pendientes": pendientes,
        "total_facturado": total, "activo": "clientes",
    })


# ------------------------------------------------------------------ paginas
@app.get("/")
def inicio(request: Request):
    with db.sesion() as conn:
        contratos = [dict(r) for r in db.listar_contratos(conn, solo_activos=False)]
        pendientes = [dict(r) for r in db.pendientes_abiertos(conn)]
        facturas = [dict(r) for r in conn.execute(
            """SELECT f.*, c.alias, c.asesor FROM facturas f
               LEFT JOIN contratos c ON c.nic = f.nic
               WHERE f.estado = 'descargada' ORDER BY f.corte DESC LIMIT 200""")]
        eventos = [dict(r) for r in db.ultimos_eventos(conn, 40)]
    return templates.TemplateResponse(request, "index.html", {
        "contratos": contratos, "pendientes": pendientes, "facturas": facturas,
        "eventos": eventos, "chat_configurado": bool(config.TELEGRAM_CHAT_ID),
        "activo": "panel",
    })


@app.post("/contratos")
def crear_contrato(nic: str = Form(...), razon_social: str = Form(default=""),
                   alias: str = Form(default=""), asesor: str = Form(default=""),
                   dia_corte: str = Form(default=""), notas: str = Form(default="")):
    nic = nic.strip()
    if not nic.isdigit():
        raise HTTPException(400, "El NIC debe ser numérico")
    with db.sesion() as conn:
        db.upsert_contrato(
            conn, nic, razon_social=razon_social,
            alias=alias, asesor=asesor, notas=notas,
            dia_corte=int(dia_corte) if dia_corte.strip().isdigit() else None,
        )
        db.log_evento(conn, "info", f"Contrato {nic} guardado desde el dashboard")
    return RedirectResponse("/", status_code=303)


@app.post("/contratos/{nic}/editar")
def editar_contrato(nic: str, razon_social: str = Form(default=""),
                    alias: str = Form(default=""), asesor: str = Form(default=""),
                    dia_corte: str = Form(default=""), notas: str = Form(default=""),
                    activo: str = Form(default="1")):
    with db.sesion() as conn:
        db.upsert_contrato(
            conn, nic, razon_social=razon_social, alias=alias, asesor=asesor, notas=notas,
            dia_corte=int(dia_corte) if dia_corte.strip().isdigit() else None,
            activo=1 if activo == "1" else 0,
        )
        db.log_evento(conn, "info", f"Contrato {nic} actualizado")
    return RedirectResponse("/", status_code=303)


@app.post("/contratos/{nic}/eliminar")
def borrar_contrato(nic: str):
    with db.sesion() as conn:
        db.eliminar_contrato(conn, nic)
        db.log_evento(conn, "aviso", f"Contrato {nic} eliminado")
    return RedirectResponse("/", status_code=303)


@app.get("/facturas/{factura_id}/pdf")
def ver_pdf(factura_id: int):
    with db.sesion() as conn:
        fila = conn.execute("SELECT pdf_path FROM facturas WHERE id = ?", (factura_id,)).fetchone()
    if not fila or not fila["pdf_path"] or not Path(fila["pdf_path"]).exists():
        raise HTTPException(404, "PDF no encontrado")
    return FileResponse(fila["pdf_path"], media_type="application/pdf",
                        filename=Path(fila["pdf_path"]).name)
