import json
from unittest.mock import MagicMock

import pytest
from app import config, telegram




@pytest.fixture
def post(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "TOKEN")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "-100123")
    m = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"ok": True, "result": [{"message_id": 1}]}
    m.return_value = resp
    monkeypatch.setattr(telegram.requests, "post", m)
    return m


def pdfs(tmp_path, n):
    out = []
    for i in range(n):
        p = tmp_path / f"f{i}.pdf"
        p.write_bytes(b"%PDF-1.4")
        out.append(p)
    return out


def test_11_archivos_dos_llamadas(post, tmp_path):
    res = telegram.enviar_album(pdfs(tmp_path, 11), caption="hola")
    assert post.call_count == 2
    assert all(c.args[0].endswith("/sendMediaGroup") for c in post.call_args_list)
    tam = [len(json.loads(c.kwargs["data"]["media"])) for c in post.call_args_list]
    assert tam == [10, 1]
    assert len(res) == 2


def test_media_usa_attach(post, tmp_path):
    telegram.enviar_album(pdfs(tmp_path, 3), caption="cap")
    kw = post.call_args.kwargs
    media = json.loads(kw["data"]["media"])
    assert [m["media"] for m in media] == ["attach://file0", "attach://file1", "attach://file2"]
    assert set(kw["files"]) == {"file0", "file1", "file2"}
    assert media[0]["caption"] == "cap" and "caption" not in media[1]
    assert kw["data"]["chat_id"] == "-100123"
    assert all(m["type"] == "document" for m in media)


def test_exactamente_10_es_un_solo_album(post, tmp_path):
    telegram.enviar_album(pdfs(tmp_path, 10))
    assert post.call_count == 1


def test_lista_vacia_no_llama(post):
    assert telegram.enviar_album([]) == []
    post.assert_not_called()


def test_sin_token_lanza_error(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "-1")
    with pytest.raises(telegram.TelegramError):
        telegram.enviar_album(pdfs(tmp_path, 1))


def test_telegram_rechaza(post, tmp_path):
    post.return_value.json.return_value = {"ok": False, "description": "bad"}
    with pytest.raises(telegram.TelegramError):
        telegram.enviar_album(pdfs(tmp_path, 1))
