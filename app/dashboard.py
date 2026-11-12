"""Dashboard web: alta de NIC, autocompletado de razón social, alias/asesor y Excel."""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, config, db, excel, naming, telegram
from .edenorte import EdenorteClient, PortalError

BASE = Path(__file__).resolve().parent


def _num(valor: str | None) -> float | None:
    """'3,5' o '3.5' -> float; vacio -> None."""
    if valor is None:
        return None
    texto = str(valor).strip().replace(",", ".")
    if not texto:
        return None
    try:
        return float(texto)
    except ValueError:
        return None


def _coordenadas(texto: str | None) -> tuple[float | None, float | None]:
    """'19.402583, -70.727444' -> (lat, lon). Acepta coma, punto y coma o espacio."""
    if not texto:
        return None, None
    limpio = str(texto).replace("(", " ").replace(")", " ").strip()
    m = (re.search(r"(-?\d{1,3}(?:[.,]\d+)?)\s*[,;]\s*(-?\d{1,3}(?:[.,]\d+)?)", limpio)
         or re.search(r"(-?\d{1,3}(?:[.,]\d+)?)\s+(-?\d{1,3}(?:[.,]\d+)?)", limpio))
    if not m:
        return None, None
    lat = _num(m.group(1))
    lon = _num(m.group(2))
    if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
        return None, None
    return lat, lon


templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.auto_reload = True  # recarga plantillas sin reiniciar el servidor

app = FastAPI(title="Enpower Solar — Facturas Edenorte",
              docs_url=None, redoc_url=None, openapi_url=None)

PUBLICAS = ("/login", "/static", "/favicon.ico")
COOKIE = "sesion"
METODOS_SEGUROS = ("GET", "HEAD", "OPTIONS")

# limite de intentos de login (por IP y por usuario)
_INTENTOS: dict[str, list[float]] = {}
MAX_INTENTOS, VENTANA = 5, 15 * 60


def _destino_seguro(next_url: str | None) -> str:
    """Solo rutas internas: evita open redirect con '//evil.com' o '/\\evil.com'."""
    destino = (next_url or "/").strip()
    if not destino.startswith("/") or destino.startswith("//") or destino.startswith("/\\"):
        return "/"
    return destino


def _demasiados_intentos(clave: str) -> bool:
    ahora = time.time()
    marcas = [t for t in _INTENTOS.get(clave, []) if ahora - t < VENTANA]
    _INTENTOS[clave] = marcas
    return len(marcas) >= MAX_INTENTOS


def _anotar_fallo(clave: str) -> None:
    _INTENTOS.setdefault(clave, []).append(time.time())


def requiere_admin(request: Request) -> None:
    """Para acciones destructivas o de configuracion: solo administradores."""
    usuario = usuario_actual(request)
    if not usuario:
        raise HTTPException(401, "Sesión no iniciada")
    if not usuario.es_admin:
        raise HTTPException(403, "Solo un administrador puede hacer esta acción")


def usuario_actual(request: Request) -> auth.Usuario | None:
    if getattr(request.state, "usuario", None):
        return request.state.usuario
    uid = auth.leer_token(request.cookies.get(COOKIE))
    if uid is None:
        return None
    with db.sesion() as conn:
        usuario = auth.obtener_usuario(conn, uid)
    if usuario and usuario.activo:
        request.state.usuario = usuario
        return usuario
    return None


@app.middleware("http")
async def exigir_sesion(request: Request, call_next):
    ruta = request.url.path

    # CSRF: en peticiones que cambian datos, el Origin/Referer debe ser el propio
    if request.method not in METODOS_SEGUROS:
        origen = request.headers.get("origin") or request.headers.get("referer") or ""
        if origen:
            host = request.headers.get("host", "")
            permitidos = {f"http://{host}", f"https://{host}"}
            if not any(origen.startswith(p) for p in permitidos):
                return JSONResponse({"ok": False, "error": "Origen no permitido"},
                                    status_code=403)

    if ruta.startswith(PUBLICAS):
        respuesta = await call_next(request)
        return _con_cabeceras(respuesta)
    usuario = usuario_actual(request)
    if usuario is None:
        if ruta.startswith("/api/"):
            return JSONResponse({"ok": False, "error": "Sesión no iniciada"}, status_code=401)
        destino = quote(ruta + (("?" + request.url.query) if request.url.query else ""))
        return RedirectResponse(f"/login?next={destino}", status_code=303)
    respuesta = await call_next(request)
    return _con_cabeceras(respuesta)


def _con_cabeceras(respuesta):
    """Cabeceras basicas de seguridad (clickjacking, sniffing, referer)."""
    respuesta.headers.setdefault("X-Frame-Options", "DENY")
    respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
    respuesta.headers.setdefault("Referrer-Policy", "same-origin")
    return respuesta


@app.on_event("startup")
def _preparar() -> None:
    db.init_db()
    with db.sesion() as conn:
        creado = auth.asegurar_admin_inicial(conn)
        if creado:
            usuario, password = creado
            db.log_evento(conn, "info", "Usuario administrador creado", usuario)
            print(f"[usuarios] admin inicial -> usuario: {usuario} / contraseña: {password}")


@app.get("/login")
def login_form(request: Request, next: str = "/"):
    if usuario_actual(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": next,
                                                              "sin_usuarios": False})


@app.post("/login")
def login(request: Request, usuario: str = Form(...), password: str = Form(...),
          next: str = Form(default="/")):
    clave = request.client.host if request.client else "?"
    if _demasiados_intentos(clave) or _demasiados_intentos("u:" + usuario.strip().lower()):
        with db.sesion() as conn:
            db.log_evento(conn, "aviso", "Demasiados intentos de acceso", clave)
        raise HTTPException(429, "Demasiados intentos. Espera unos minutos.")
    with db.sesion() as conn:
        encontrado = auth.autenticar(conn, usuario, password)
        if encontrado:
            auth.marcar_acceso(conn, encontrado.id)
            db.log_evento(conn, "info", f"Acceso de {encontrado.usuario}")
        else:
            db.log_evento(conn, "aviso", f"Intento de acceso fallido: {usuario[:40]}")
            _anotar_fallo(clave)
            _anotar_fallo("u:" + usuario.strip().lower())
    if not encontrado:
        return templates.TemplateResponse(request, "login.html",
                                          {"next": next, "error": "Usuario o contraseña incorrectos",
                                           "usuario_previo": usuario}, status_code=401)
    respuesta = RedirectResponse(_destino_seguro(next), status_code=303)
    # secure=True cuando el panel va por HTTPS (tunel/VPN): la cookie no viaja
    # nunca por http. En red local sin HTTPS se deja en False o no se podria entrar.
    _INTENTOS.pop(clave, None)
    _INTENTOS.pop("u:" + usuario.strip().lower(), None)
    https = config.COOKIE_SECURE or request.url.scheme == "https"
    respuesta.set_cookie(COOKIE, auth.crear_token(encontrado.id), httponly=True,
                         samesite="lax", secure=https, max_age=auth.DURACION_SESION)
    return respuesta


@app.get("/logout")
def logout():
    respuesta = RedirectResponse("/login", status_code=303)
    respuesta.delete_cookie(COOKIE)
    return respuesta


@app.get("/usuarios")
def usuarios_page(request: Request):
    usuario = usuario_actual(request)
    if not usuario:
        return RedirectResponse("/login", status_code=303)
    if not usuario.es_admin:
        return RedirectResponse("/?sinpermiso=1", status_code=303)
    with db.sesion() as conn:
        lista = [dict(u) for u in auth.listar_usuarios(conn)]
    return templates.TemplateResponse(request, "usuarios.html",
                                      {"activo": "usuarios", "usuarios": lista,
                                       "usuario": usuario})


@app.post("/mi-clave")
def mi_clave(request: Request, actual: str = Form(...), nueva: str = Form(...),
             repetir: str = Form(...)):
    """Cada usuario cambia su propia contraseña."""
    usuario = usuario_actual(request)
    if not usuario:
        return RedirectResponse("/login", status_code=303)
    if nueva != repetir or len(nueva) < 8:
        return RedirectResponse("/usuarios?error=clave", status_code=303)
    with db.sesion() as conn:
        if not auth.autenticar(conn, usuario.usuario, actual):
            return RedirectResponse("/usuarios?error=actual", status_code=303)
        auth.cambiar_password(conn, usuario.id, nueva)
        db.log_evento(conn, "info", f"{usuario.usuario} cambió su contraseña")
    return RedirectResponse("/usuarios?clave=1", status_code=303)


@app.post("/usuarios")
def usuarios_crear(request: Request, usuario_nuevo: str = Form(...),
                   password: str = Form(...), nombre: str = Form(default=""),
                   rol: str = Form(default="asesor")):
    actual = usuario_actual(request)
    if not actual or not actual.es_admin:
        raise HTTPException(403, "Solo un administrador puede crear usuarios")
    if len(password) < 8:
        raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres")
    if rol not in ("admin", "asesor"):
        raise HTTPException(400, "Rol no válido (admin o asesor)")
    nombre_usuario = usuario_nuevo.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,32}", nombre_usuario):
        raise HTTPException(400, "El usuario debe tener 3-32 caracteres: letras, números, . _ -")
    with db.sesion() as conn:
        if auth.existe_usuario(conn, nombre_usuario):
            raise HTTPException(409, "Ese usuario ya existe")
        auth.crear_usuario(conn, nombre_usuario, password, nombre, rol)
        db.log_evento(conn, "info", f"Usuario {usuario_nuevo} creado por {actual.usuario}")
    return RedirectResponse("/usuarios", status_code=303)


@app.post("/usuarios/{usuario_id}/password")
def usuarios_password(request: Request, usuario_id: int, password: str = Form(...)):
    actual = usuario_actual(request)
    if not actual or not actual.es_admin:
        raise HTTPException(403, "Solo un administrador puede cambiar contraseñas")
    if len(password) < 8:
        raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres")
    with db.sesion() as conn:
        auth.cambiar_password(conn, usuario_id, password)
        db.log_evento(conn, "aviso", f"Contraseña cambiada por {actual.usuario}",
                      f"usuario_id={usuario_id}")
    return RedirectResponse("/usuarios", status_code=303)


@app.post("/usuarios/{usuario_id}/estado")
def usuarios_estado(request: Request, usuario_id: int, activo: str = Form(...)):
    actual = usuario_actual(request)
    if not actual or not actual.es_admin:
        raise HTTPException(403, "Solo un administrador puede cambiar el estado")
    if usuario_id == actual.id:
        raise HTTPException(400, "No puedes desactivar tu propio usuario")
    with db.sesion() as conn:
        conn.execute("UPDATE usuarios SET activo = ? WHERE id = ?",
                     (1 if activo == "1" else 0, usuario_id))
        conn.commit()
    return RedirectResponse("/usuarios", status_code=303)


@app.post("/clientes/{nic}/proyectos")
def proyectos_crear(nic: str, nombre: str = Form(default=""),
                    paneles: str = Form(default=""), capacidad_instalada: str = Form(default=""),
                    inversores: str = Form(default=""), bateria: str = Form(default=""),
                    capacidad_bateria: str = Form(default=""),
                    fecha_instalacion: str = Form(default=""), asesor: str = Form(default=""),
                    notas: str = Form(default="")):
    with db.sesion() as conn:
        if db.obtener_contrato(conn, nic) is None:
            raise HTTPException(404, "Ese NIC no está registrado")
        db.crear_proyecto(conn, {
            "nic": nic, "nombre": nombre.strip(), "paneles": paneles.strip(),
            "capacidad_instalada": _num(capacidad_instalada),
            "inversores": inversores.strip(), "bateria": bateria.strip(),
            "capacidad_bateria": _num(capacidad_bateria),
            "fecha_instalacion": fecha_instalacion.strip(),
            "asesor": asesor.strip(), "notas": notas.strip(),
        })
        db.log_evento(conn, "info", f"Proyecto instalado agregado a {nic}", nombre[:60])
    return RedirectResponse(f"/clientes/{nic}#proyectos", status_code=303)


@app.post("/proyectos/{proyecto_id}/eliminar")
def proyectos_eliminar(proyecto_id: int, request: Request):
    requiere_admin(request)
    with db.sesion() as conn:
        fila = conn.execute("SELECT nic FROM proyectos WHERE id = ?",
                            (proyecto_id,)).fetchone()
        db.eliminar_proyecto(conn, proyecto_id)
        destino = f"/clientes/{fila['nic']}#proyectos" if fila else "/clientes"
    return RedirectResponse(destino, status_code=303)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


# ------------------------------------------------------------------ API datos
@app.get("/api/contrato/{nic}")
def api_contrato(nic: str, proveedor: str = "edenorte",
                 referencia: str | None = None) -> JSONResponse:
    """Busca el NIC en la cuenta; si no esta, lo asocia y espera la confirmacion.

    `?proveedor=edesur&referencia=2115589310` valida contra Edesur. La asociacion
    en el portal de Edenorte tarda unos segundos, asi que puede tardar ~1 minuto.
    """
    nic = str(nic).strip()
    proveedor = (proveedor or "edenorte").strip().lower()
    try:
        from .proveedores import portal_de
        cliente = portal_de(proveedor)
        cliente.login()
        contrato = next((c for c in cliente.contratos() if c.nic == nic), None)
        asociado = False
        if contrato is None:
            if proveedor == "edesur":
                contrato = cliente.asociar_contrato(nic, referencia=referencia)
            else:
                contrato = cliente.asociar_contrato(nic)
            asociado = True
        with db.sesion() as conn:
            db.upsert_contrato(conn, contrato.nic, razon_social=contrato.titular,
                               direccion=contrato.direccion, telefono=contrato.telefono,
                               proveedor=proveedor)
            if asociado:
                db.log_evento(conn, "info", f"Contrato {nic} asociado a la cuenta",
                              contrato.titular)
        if asociado:
            try:
                import threading
                from .runner import sincronizar_historial
                threading.Thread(target=sincronizar_historial,
                                 kwargs={"enviar": False, "nic": contrato.nic},
                                 daemon=True).start()
            except Exception:  # noqa: BLE001
                pass
        return JSONResponse({
            "ok": True, "nic": contrato.nic, "razon_social": contrato.titular,
            "nombre_normalizado": naming.normalizar_razon_social(contrato.titular),
            "direccion": contrato.direccion, "telefono": contrato.telefono,
            "asociado": asociado, "proveedor": proveedor,
        })
    except PortalError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    except Exception as exc:  # noqa: BLE001 - red, timeouts, lo que sea
        return JSONResponse({"ok": False, "error": f"Fallo la consulta: {exc}"[:300]},
                            status_code=502)


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


@app.get("/mapa")
def mapa_page(request: Request):
    with db.sesion() as conn:
        clientes = db.clientes_mapa(conn)
    return templates.TemplateResponse(request, "mapa.html", {
        "activo": "mapa", "clientes": clientes,
        "con_ubicacion": [c for c in clientes if c.get("latitud") and c.get("longitud")],
    })


@app.get("/api/mapa")
def api_mapa() -> JSONResponse:
    with db.sesion() as conn:
        clientes = db.clientes_mapa(conn)
    return JSONResponse({"clientes": clientes})


@app.post("/api/geocodificar")
def api_geocodificar(direccion: str = Form(...),
                     contexto: str = Form(default="Santiago, República Dominicana")) -> JSONResponse:
    from .geo import geocodificar
    resultado = geocodificar(direccion, contexto)
    return JSONResponse(resultado, status_code=200 if resultado.get("ok") else 400)


@app.post("/api/reverse")
def api_reverse(lat: str = Form(...), lon: str = Form(...)) -> JSONResponse:
    """Coordenadas -> direccion de la calle (Nominatim /reverse)."""
    from .geo import enlace_mapa, reverso
    la, lo = _num(lat), _num(lon)
    if la is None or lo is None:
        return JSONResponse({"ok": False, "error": "Coordenadas inválidas"}, status_code=400)
    resultado = reverso(la, lo)
    if resultado.get("ok"):
        resultado["enlace"] = enlace_mapa(la, lo)
    return JSONResponse(resultado, status_code=200 if resultado.get("ok") else 400)


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
def api_test_telegram(request: Request) -> JSONResponse:
    requiere_admin(request)
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
    canales, vistos = [], set()
    for upd in pendientes:
        msg = upd.get("channel_post") or upd.get("message") or upd.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid and cid not in vistos:
            vistos.add(cid)
            canales.append({"tipo": chat.get("type"), "id": cid,
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
def api_telegram_detectar(request: Request) -> JSONResponse:
    requiere_admin(request)
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
def api_telegram_chat(request: Request, chat_id: str = Form(...)) -> JSONResponse:
    requiere_admin(request)
    chat_id = chat_id.strip()
    if not chat_id:
        return JSONResponse({"ok": False, "error": "chat_id vacío"}, status_code=400)
    config.guardar_chat_id(chat_id)
    return JSONResponse({"ok": True, "chat_id": chat_id})


@app.post("/api/corrida")
def api_corrida(request: Request, fecha: str | None = Form(default=None)) -> JSONResponse:
    requiere_admin(request)
    from .runner import corrida
    f = datetime.strptime(fecha, "%Y-%m-%d").date() if fecha else None
    try:
        r = corrida(fecha=f, enviar=True)
        return JSONResponse({"ok": r.portal_error is None, **r.como_dict()})
    except Exception as exc:  # el dashboard nunca debe caerse
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/historial")
def api_historial(request: Request, nic: str = Form(default=""),
                  enviar: str = Form(default="1")) -> JSONResponse:
    requiere_admin(request)
    """Guarda las ultimas facturas (de todos los clientes o de uno) y envia."""
    from .runner import sincronizar_historial
    try:
        r = sincronizar_historial(enviar=enviar.strip() not in ("0", "false", ""),
                                  nic=nic.strip() or None)
        return JSONResponse({"ok": True, "nuevos": len(r.get("nuevos", [])),
                             "enviados": r.get("enviados", 0),
                             "errores": r.get("errores", [])[:5], **r})
    except Exception as exc:  # el dashboard nunca debe caerse
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/export/excel")
def exportar_excel(request: Request):
    requiere_admin(request)
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
    from .proveedores import NOMBRES
    return templates.TemplateResponse(request, "cliente_nuevo.html",
                                      {"activo": "cliente_nuevo",
                                       "proveedores": NOMBRES})


@app.post("/clientes")
def crear_cliente(nic: str = Form(...), razon_social: str = Form(default=""),
                  proveedor: str = Form(default="edenorte"),
                  referencia: str = Form(default=""),
                  alias: str = Form(default=""), asesor: str = Form(default=""),
                  dia_corte: str = Form(default=""), notas: str = Form(default=""),
                  direccion: str = Form(default=""), telefono: str = Form(default=""),
                  latitud: str = Form(default=""), longitud: str = Form(default=""),
                  paneles: str = Form(default=""),
                  potencia_paneles: str = Form(default=""), inversores: str = Form(default=""),
                  bateria: str = Form(default=""), capacidad_bateria: str = Form(default=""),
                  fecha_instalacion: str = Form(default="")):
    nic = nic.strip()
    if not nic.isdigit():
        raise HTTPException(400, "El NIC debe ser numérico")
    proveedor = (proveedor or "edenorte").strip().lower()
    if proveedor not in ("edenorte", "edesur"):
        raise HTTPException(400, "Distribuidora no válida")

    # Edesur: la referencia de pago es obligatoria y el nombre lo da el portal
    if proveedor == "edesur" and not referencia.strip():
        raise HTTPException(400, "La referencia de pago es obligatoria para Edesur")

    aviso = ""
    if proveedor == "edenorte" and not razon_social.strip():
        # no pulso "Buscar": se valida (y asocia) contra el portal para no
        # guardar un NIC inventado ni dejarlo vacio
        try:
            from .proveedores import portal_de
            portal = portal_de("edenorte")
            portal.login()
            contrato = next((c for c in portal.contratos() if c.nic == nic), None)
            if contrato is None:
                contrato = portal.asociar_contrato(nic, espera_max=15)
            razon_social = contrato.titular
            direccion = direccion or contrato.direccion
            telefono = telefono or contrato.telefono
        except Exception as exc:  # noqa: BLE001 - se guarda y se avisa
            aviso = f"No se pudo validar en Edenorte: {str(exc)[:200]}"

    if proveedor == "edesur" and referencia.strip():
        try:
            from .proveedores import EdesurPortal
            portal = EdesurPortal()
            portal.login()
            contrato = portal.asociar_contrato(nic, referencia=referencia.strip())
            razon_social = razon_social or contrato.titular
            direccion = direccion or contrato.direccion
        except Exception as exc:  # el cliente se guarda igual, con aviso
            aviso = f"No se pudo validar en Edesur: {str(exc)[:200]}"

    with db.sesion() as conn:
        db.upsert_contrato(
            conn, nic, razon_social=razon_social, alias=alias, asesor=asesor,
            proveedor=proveedor,
            notas=notas, direccion=direccion, telefono=telefono,
            latitud=_num(latitud), longitud=_num(longitud), paneles=paneles,
            potencia_paneles=_num(potencia_paneles), inversores=inversores,
            bateria=bateria, capacidad_bateria=_num(capacidad_bateria),
            fecha_instalacion=fecha_instalacion,
            dia_corte=int(dia_corte) if dia_corte.strip().isdigit() else None,
        )
        db.log_evento(conn, "info",
                      f"Cliente {nic} agregado desde el módulo de clientes ({proveedor})",
                      aviso)
    # traer sus facturas ya mismo (sin mandar nada a Telegram)
    if proveedor == "edenorte" or referencia.strip():
        try:
            import threading
            from .runner import sincronizar_historial
            threading.Thread(target=sincronizar_historial,
                             kwargs={"enviar": False, "nic": nic}, daemon=True).start()
        except Exception as exc:  # noqa: BLE001 - el alta ya quedo guardada
            with db.sesion() as conn:
                db.log_evento(conn, "aviso", f"No se pudo iniciar la descarga de {nic}",
                              str(exc)[:200])
    return RedirectResponse(f"/clientes/{nic}", status_code=303)


@app.post("/clientes/{nic}/equipo")
def guardar_equipo(nic: str, direccion: str = Form(default=""),
                   telefono: str = Form(default=""), latitud: str = Form(default=""),
                   longitud: str = Form(default=""), paneles: str = Form(default=""),
                   potencia_paneles: str = Form(default=""),
                   inversores: str = Form(default=""), bateria: str = Form(default=""),
                   capacidad_bateria: str = Form(default=""),
                   fecha_instalacion: str = Form(default="")):
    """Guarda ubicacion y equipo desde la ficha del cliente (reemplaza valores)."""
    with db.sesion() as conn:
        if db.obtener_contrato(conn, nic) is None:
            raise HTTPException(404, "Ese NIC no está registrado")
        db.actualizar_equipo(conn, nic, {
            "direccion": direccion.strip(), "telefono": telefono.strip(),
            "latitud": _num(latitud), "longitud": _num(longitud),
            "paneles": paneles.strip(), "potencia_paneles": _num(potencia_paneles),
            "inversores": inversores.strip(), "bateria": bateria.strip(),
            "capacidad_bateria": _num(capacidad_bateria),
            "fecha_instalacion": fecha_instalacion.strip(),
        })
        db.log_evento(conn, "info", f"Equipo de {nic} actualizado",
                      f"paneles={potencia_paneles} inversores={inversores[:40]}")
    return RedirectResponse(f"/clientes/{nic}?guardado=1", status_code=303)


@app.get("/clientes/{nic}/editar")
def cliente_editar(request: Request, nic: str):
    """Ficha completa editable del cliente (solo administradores)."""
    requiere_admin(request)
    with db.sesion() as conn:
        contrato = db.obtener_contrato(conn, nic)
    if contrato is None:
        raise HTTPException(404, "Ese NIC no está registrado")
    from .proveedores import NOMBRES
    return templates.TemplateResponse(request, "cliente_editar.html", {
        "activo": "clientes", "cliente": dict(contrato), "proveedores": NOMBRES,
    })


@app.post("/clientes/{nic}/editar")
def cliente_editar_guardar(request: Request, nic: str,
                           razon_social: str = Form(default=""),
                           alias: str = Form(default=""),
                           asesor: str = Form(default=""),
                           dia_corte: str = Form(default=""),
                           notas: str = Form(default=""),
                           proveedor: str = Form(default="edenorte"),
                           direccion: str = Form(default=""),
                           telefono: str = Form(default=""),
                           coordenadas: str = Form(default=""),
                           latitud: str = Form(default=""),
                           longitud: str = Form(default=""),
                           paneles: str = Form(default=""),
                           potencia_paneles: str = Form(default=""),
                           inversores: str = Form(default=""),
                           bateria: str = Form(default=""),
                           capacidad_bateria: str = Form(default=""),
                           fecha_instalacion: str = Form(default=""),
                           activo: str = Form(default="1")):
    """Guarda la ficha completa: lo que venga vacio se guarda vacio."""
    requiere_admin(request)
    proveedor = (proveedor or "edenorte").strip().lower()
    if proveedor not in ("edenorte", "edesur"):
        raise HTTPException(400, "Distribuidora no válida")
    dia = int(dia_corte) if dia_corte.strip().isdigit() else None
    if dia is not None and not 1 <= dia <= 31:
        raise HTTPException(400, "El día de corte debe estar entre 1 y 31")
    lat, lon = _coordenadas(coordenadas) if coordenadas.strip() else (None, None)
    if coordenadas.strip() and lat is None:
        raise HTTPException(400, "Coordenadas inválidas: pega \"latitud, longitud\" "
                                 "(ej. 19.402583, -70.727444)")
    if lat is None:                       # sin el campo unico se usan los ocultos
        lat, lon = _num(latitud), _num(longitud)

    with db.sesion() as conn:
        if db.obtener_contrato(conn, nic) is None:
            raise HTTPException(404, "Ese NIC no está registrado")
        db.actualizar_contrato(conn, nic, {
            "razon_social": razon_social.strip(), "alias": alias.strip(),
            "asesor": asesor.strip(), "dia_corte": dia, "notas": notas.strip(),
            "proveedor": proveedor, "direccion": direccion.strip(),
            "telefono": telefono.strip(), "latitud": lat,
            "longitud": lon, "paneles": paneles.strip(),
            "potencia_paneles": _num(potencia_paneles), "inversores": inversores.strip(),
            "bateria": bateria.strip(), "capacidad_bateria": _num(capacidad_bateria),
            "fecha_instalacion": fecha_instalacion.strip(),
            "activo": 1 if activo in ("1", "on", "true") else 0,
        })
        db.log_evento(conn, "info", f"Ficha del cliente {nic} editada por "
                                    f"{usuario_actual(request).usuario}")
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
        proyectos = [dict(p) for p in db.listar_proyectos(conn, nic)]
    datos = dict(contrato)
    datos["proximo_corte"] = proximo_corte(datos.get("dia_corte"))
    datos["dias_corte"] = dias_para_corte(datos.get("dia_corte"))
    total = sum(f["facturado"] or 0 for f in historico)
    return templates.TemplateResponse(request, "cliente_detalle.html", {
        "cliente": datos, "historico": historico, "pendientes": pendientes,
        "total_facturado": total, "activo": "clientes", "proyectos": proyectos,
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
def editar_contrato(request: Request, nic: str, razon_social: str = Form(default=""),
                    alias: str = Form(default=""), asesor: str = Form(default=""),
                    dia_corte: str = Form(default=""), notas: str = Form(default=""),
                    activo: str = Form(default="1"), direccion: str = Form(default=""),
                    telefono: str = Form(default=""), latitud: str = Form(default=""),
                    longitud: str = Form(default=""), paneles: str = Form(default=""),
                    potencia_paneles: str = Form(default=""), inversores: str = Form(default=""),
                    bateria: str = Form(default=""), capacidad_bateria: str = Form(default=""),
                    fecha_instalacion: str = Form(default="")):
    requiere_admin(request)
    with db.sesion() as conn:
        db.actualizar_contrato(conn, nic, {
            "razon_social": razon_social.strip(), "alias": alias.strip(),
            "asesor": asesor.strip(), "notas": notas.strip(),
            "dia_corte": int(dia_corte) if dia_corte.strip().isdigit() else None,
            "activo": 1 if activo == "1" else 0,
            "direccion": direccion.strip(), "telefono": telefono.strip(),
            "latitud": _num(latitud), "longitud": _num(longitud),
            "paneles": paneles.strip(), "potencia_paneles": _num(potencia_paneles),
            "inversores": inversores.strip(), "bateria": bateria.strip(),
            "capacidad_bateria": _num(capacidad_bateria),
            "fecha_instalacion": fecha_instalacion.strip(),
        })
        db.log_evento(conn, "info", f"Contrato {nic} actualizado")
    return RedirectResponse("/", status_code=303)


@app.post("/contratos/{nic}/eliminar")
def borrar_contrato(nic: str, request: Request):
    requiere_admin(request)
    with db.sesion() as conn:
        db.eliminar_contrato(conn, nic)
        db.log_evento(conn, "aviso", f"Contrato {nic} eliminado")
    return RedirectResponse("/", status_code=303)


@app.get("/facturas/{factura_id}/pdf")
def ver_pdf(factura_id: int):
    with db.sesion() as conn:
        fila = conn.execute("SELECT pdf_path FROM facturas WHERE id = ?", (factura_id,)).fetchone()
    if not fila or not fila["pdf_path"]:
        raise HTTPException(404, "PDF no encontrado")
    ruta = Path(fila["pdf_path"]).resolve()
    # solo se sirven PDFs de la carpeta del proyecto (nada de rutas de fuera)
    if not ruta.is_relative_to(config.FACTURAS_DIR.resolve()) or not ruta.exists():
        raise HTTPException(404, "PDF no encontrado")
    return FileResponse(fila["pdf_path"], media_type="application/pdf",
                        filename=Path(fila["pdf_path"]).name)
