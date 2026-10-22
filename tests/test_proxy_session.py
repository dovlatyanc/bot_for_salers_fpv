"""Тесты фабрики сессии бота с прокси (config.make_bot_session) — без сети.

PROXY_URL читается из config при вызове, поэтому в тестах подменяем атрибут
модуля config через monkeypatch (сам config уже импортирован conftest-ом).
"""

import pytest
from aiogram.client.session.aiohttp import AiohttpSession

import config
from config import mask_proxy_url, make_bot_session

aiohttp_socks = pytest.importorskip("aiohttp_socks")


def test_socks5_makes_session_with_proxy_connector(monkeypatch):
    monkeypatch.setattr(config, "PROXY_URL", "socks5://u:p@127.0.0.1:1080")
    session = make_bot_session()
    assert isinstance(session, AiohttpSession)
    # aiogram строит SOCKS-коннектор через aiohttp-socks
    assert issubclass(session._connector_type, aiohttp_socks.ProxyConnector)
    assert session.proxy == "socks5://u:p@127.0.0.1:1080"


def test_socks4_also_supported(monkeypatch):
    monkeypatch.setattr(config, "PROXY_URL", "socks4://127.0.0.1:1080")
    session = make_bot_session()
    assert issubclass(session._connector_type, aiohttp_socks.ProxyConnector)


def test_http_proxy(monkeypatch):
    url = "http://u:p@127.0.0.1:8080"
    monkeypatch.setattr(config, "PROXY_URL", url)
    session = make_bot_session()
    assert isinstance(session, AiohttpSession)
    assert session.proxy == url


def test_empty_url_returns_none(monkeypatch):
    monkeypatch.setattr(config, "PROXY_URL", "")
    assert make_bot_session() is None
    monkeypatch.setattr(config, "PROXY_URL", "   ")
    assert make_bot_session() is None


def test_unsupported_scheme_exits_with_message(monkeypatch):
    monkeypatch.setattr(config, "PROXY_URL", "ftp://127.0.0.1:21")
    with pytest.raises(SystemExit) as ei:
        make_bot_session()
    assert "неподдерживаемая схема" in str(ei.value)
    assert "ftp" in str(ei.value)


def test_mask_proxy_url_with_credentials():
    assert (
        mask_proxy_url("socks5://user:password@host:1080")
        == "socks5://***@host:1080"
    )


def test_mask_proxy_url_without_credentials():
    assert mask_proxy_url("socks5://host:1080") == "socks5://host:1080"
    assert mask_proxy_url("http://host:8080") == "http://host:8080"
