"""Envio a Telegram: mensajes y albumes de PDFs (todas las facturas del dia juntas)."""
from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path

import requests

from . import config

API = "https://api.telegram.org/bot{token}/{method}"
MAX_ALBUM = 10  # limite de la Bot API por sendMediaGroup


class TelegramError(RuntimeError):
    pass


def _token() -> str:
    if not config.TELEGRAM_BOT_TOKEN:
        raise TelegramError("Falta TELEGRAM_BOT_TOKEN en .env")
    return config.TELEGRAM_BOT_TOKEN


def _url(method: str) -> str:
    return API.format(token=_token(), method=method)


def _ok(resp: requests.Response) -> dict:
    try:
        datos = resp.json()
    except ValueError:
        raise TelegramError(f"Respuesta no JSON de Telegram ({resp.status_code})")
    if not datos.get("ok"):
        codigo = datos.get("error_code")
        espera = (datos.get("parameters") or {}).get("retry_after")
        extra = f" — esperar {espera} s (flood control)" if espera else ""
        raise TelegramError(
            f"Telegram rechazo la peticion ({codigo}): {datos.get('description')}{extra}")
    return datos["result"]


def _retry_after(exc: Exception) -> int | None:
    """Segundos que pide Telegram cuando aplica flood control."""
    texto = str(exc)
    if "flood control" not in texto:
        return None
    import re as _re
    m = _re.search(r"esperar (\d+) s", texto)
    return int(m.group(1)) if m else None


def enviar_mensaje(texto: str, chat_id: str | None = None, parse_mode: str = "HTML") -> dict:
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    if not chat_id:
        raise TelegramError("Falta TELEGRAM_CHAT_ID (configura el canal)")
    datos = {"chat_id": chat_id, "text": texto[:4096], "parse_mode": parse_mode,
             "disable_web_page_preview": "true"}
    for intento in (1, 2):
        resp = requests.post(_url("sendMessage"), data=datos, timeout=60)
        try:
            return _ok(resp)
        except TelegramError as exc:
            espera = _retry_after(exc)
            if espera is None or intento == 2:
                raise
            time.sleep(min(espera, 60))
    raise TelegramError("sendMessage: sin respuesta")


def enviar_documento(path: str | Path, caption: str = "", chat_id: str | None = None) -> dict:
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    if not chat_id:
        raise TelegramError("Falta TELEGRAM_CHAT_ID (configura el canal)")
    path = Path(path)
    with path.open("rb") as fh:
        resp = requests.post(_url("sendDocument"), data={
            "chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML",
        }, files={"document": (path.name, fh, mimetypes.guess_type(path.name)[0] or "application/pdf")},
        timeout=180)
    return _ok(resp)


def enviar_album(paths: list[str | Path], caption: str = "",
                 chat_id: str | None = None) -> list[dict]:
    """Manda todos los PDFs en un solo album (bloques de 10 si hay mas)."""
    if not paths:
        return []
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    if not chat_id:
        raise TelegramError("Falta TELEGRAM_CHAT_ID (configura el canal)")

    resultados: list[dict] = []
    archivos = [Path(p) for p in paths]
    for inicio in range(0, len(archivos), MAX_ALBUM):
        bloque = archivos[inicio:inicio + MAX_ALBUM]
        media = []
        for i, path in enumerate(bloque):
            item = {"type": "document", "media": f"attach://file{i}"}
            if i == 0 and caption:
                item["caption"] = caption[:1024]
                item["parse_mode"] = "HTML"
            media.append(item)

        def subir() -> requests.Response:
            files, abiertos = {}, []
            for i, path in enumerate(bloque):
                handle = path.open("rb")
                abiertos.append(handle)
                files[f"file{i}"] = (path.name, handle,
                                     mimetypes.guess_type(path.name)[0] or "application/pdf")
            try:
                return requests.post(_url("sendMediaGroup"), data={
                    "chat_id": chat_id, "media": json.dumps(media),
                }, files=files, timeout=300)
            finally:
                for handle in abiertos:
                    try:
                        handle.close()
                    except Exception:
                        pass

        resultado = None
        for intento in (1, 2):
            try:
                resultado = _ok(subir())
                break
            except TelegramError as exc:
                espera = _retry_after(exc)
                if espera is None or intento == 2:
                    raise
                time.sleep(min(espera, 60))
        if resultado is not None:
            resultados.extend(resultado if isinstance(resultado, list) else [resultado])
    return resultados


def info_bot() -> dict:
    return _ok(requests.get(_url("getMe"), timeout=30))


def updates(limite: int = 50) -> list[dict]:
    return _ok(requests.get(_url("getUpdates"), params={"limit": limite}, timeout=30))


def resolver_chat_id(chat_id: str | None = None) -> str:
    """Acepta -100xxxx, @usuario o vacio (busca el primer canal donde este el bot)."""
    objetivo = (chat_id or config.TELEGRAM_CHAT_ID or "").strip()
    if objetivo:
        return objetivo
    for upd in updates():
        msg = upd.get("channel_post") or upd.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") in {"channel", "supergroup", "group"} and chat.get("id"):
            return str(chat["id"])
    raise TelegramError("No hay canal configurado: agrega el bot como administrador "
                        "del canal y publica algo, o define TELEGRAM_CHAT_ID")
