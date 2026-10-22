"""services/ai_providers без сети: фабрика, Cloudflare, YandexGPT, фасад ai."""

import asyncio

import pytest

import ai.ai as ai_facade
import services.ai_providers.cloudflare as cf
import services.ai_providers.resilience as rs
import services.ai_providers.yandex as yx
from services.ai_providers import (
    AIProvider, AIProviderError, CloudflareAIProvider, YandexGPTProvider,
    get_ai_provider,
)
from services.ai_providers.prompts import AI_FALLBACK_PHRASES
from services.ai_providers.factory import PROVIDER_REGISTRY
from tests.fakes_http import FakeResp, make_session_class

AUDIO = b"fake-audio-bytes"


# ---------- фабрика ----------

def test_registry_and_factory():
    assert set(PROVIDER_REGISTRY) == {"cloudflare", "yandex"}
    assert isinstance(get_ai_provider("cloudflare"), CloudflareAIProvider)
    assert isinstance(get_ai_provider("yandex"), YandexGPTProvider)
    with pytest.raises(SystemExit):
        get_ai_provider("unknown-provider")


# ---------- Cloudflare ----------

async def test_cloudflare_not_configured():
    p = CloudflareAIProvider(base_url="", token="")
    assert not p.is_configured
    assert await p.ask("вопрос") == cf.NOT_CONFIGURED_HINT
    with pytest.raises(AIProviderError):
        await p.transcribe(AUDIO)


async def test_cloudflare_ask_ok(monkeypatch):
    sess, calls = make_session_class(FakeResp(payload={"answer": "ответ"}))
    monkeypatch.setattr(cf.aiohttp, "ClientSession", sess)
    p = CloudflareAIProvider(base_url="https://proxy.example", token="tok")
    assert await p.ask("вопрос") == "ответ"
    assert calls[0]["url"] == "https://proxy.example"
    assert calls[0]["json"] == {"question": "вопрос", "source": cf.SOURCE}
    assert calls[0]["headers"]["Authorization"] == "Bearer tok"


async def test_cloudflare_ask_without_token_has_no_auth_header(monkeypatch):
    sess, calls = make_session_class(FakeResp(payload={"answer": "x"}))
    monkeypatch.setattr(cf.aiohttp, "ClientSession", sess)
    await CloudflareAIProvider(base_url="https://proxy.example").ask("в")
    assert "Authorization" not in calls[0]["headers"]


async def test_cloudflare_empty_answer(monkeypatch):
    sess, _ = make_session_class(FakeResp(payload={"answer": ""}))
    monkeypatch.setattr(cf.aiohttp, "ClientSession", sess)
    p = CloudflareAIProvider(base_url="https://proxy.example", token="t")
    assert await p.ask("вопрос") == "AI вернул пустой ответ."


async def test_cloudflare_http_error(monkeypatch):
    sess, _ = make_session_class(FakeResp(status=500, text="boom"))
    monkeypatch.setattr(cf.aiohttp, "ClientSession", sess)
    p = CloudflareAIProvider(base_url="https://proxy.example", token="t")
    with pytest.raises(AIProviderError) as ei:
        await p.ask("вопрос")
    assert ei.value.status == 500 and "boom" in ei.value.body


async def test_cloudflare_transcribe_ok(monkeypatch):
    sess, calls = make_session_class(FakeResp(payload={"text": "  привет  "}))
    monkeypatch.setattr(cf.aiohttp, "ClientSession", sess)
    p = CloudflareAIProvider(base_url="https://proxy.example", token="t")
    assert await p.transcribe(AUDIO) == "привет"
    assert calls[0]["url"] == "https://proxy.example/transcribe"
    assert calls[0]["data"] == AUDIO


async def test_cloudflare_network_error(monkeypatch):
    sess, _ = make_session_class(RuntimeError("net down"))
    monkeypatch.setattr(cf.aiohttp, "ClientSession", sess)
    p = CloudflareAIProvider(base_url="https://proxy.example", token="t")
    with pytest.raises(AIProviderError, match="Ошибка запроса к AI"):
        await p.ask("вопрос")


# ---------- YandexGPT ----------

@pytest.fixture
def fast_poll(monkeypatch):
    """Ускоряем опрос/ретраи; monkeypatch восстановит значения после теста."""
    monkeypatch.setattr(yx, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(yx, "RETRY_BACKOFF", 0.001)


def _ok_completion():
    return FakeResp(payload={"result": {"alternatives": [
        {"message": {"role": "assistant", "text": "ответ"}},
    ]}})


def _provider():
    return YandexGPTProvider(api_key="ykey", folder_id="fld", model="yandexgpt")


async def test_yandex_ask_ok(fast_poll, monkeypatch):
    sess, calls = make_session_class(_ok_completion())
    monkeypatch.setattr(yx.aiohttp, "ClientSession", sess)
    p = _provider()
    assert await p.ask("вопрос") == "ответ"
    req = calls[0]
    assert req["method"] == "POST" and req["url"] == yx.COMPLETION_URL
    assert req["json"]["modelUri"] == "gpt://fld/yandexgpt/rc"  # /rc, не /latest
    assert req["headers"]["Authorization"] == "Api-Key ykey"
    assert req["json"]["messages"][1] == {"role": "user", "text": "вопрос"}


async def test_yandex_retry_on_429(fast_poll, monkeypatch):
    sess, calls = make_session_class(FakeResp(status=429, text="qps"), _ok_completion())
    monkeypatch.setattr(yx.aiohttp, "ClientSession", sess)
    assert await _provider().ask("в") == "ответ"
    assert len(calls) == 2  # одна повторная попытка


async def test_yandex_no_retry_on_400(fast_poll, monkeypatch):
    sess, calls = make_session_class(FakeResp(status=400, text="bad request"))
    monkeypatch.setattr(yx.aiohttp, "ClientSession", sess)
    with pytest.raises(AIProviderError) as ei:
        await _provider().ask("в")
    assert ei.value.status == 400
    assert len(calls) == 1  # 4xx (кроме 429) не ретраятся


async def test_yandex_timeout_becomes_provider_error(fast_poll, monkeypatch):
    sess, calls = make_session_class(
        asyncio.TimeoutError(), asyncio.TimeoutError(), asyncio.TimeoutError(),
    )
    monkeypatch.setattr(yx.aiohttp, "ClientSession", sess)
    with pytest.raises(AIProviderError, match="Yandex Cloud timeout"):
        await _provider().ask("в")
    assert len(calls) == yx.RETRY_ATTEMPTS


async def test_yandex_unexpected_payload(fast_poll, monkeypatch):
    sess, _ = make_session_class(FakeResp(payload={"nope": 1}))
    monkeypatch.setattr(yx.aiohttp, "ClientSession", sess)
    with pytest.raises(AIProviderError, match="неожиданный ответ"):
        await _provider().ask("в")


async def test_yandex_transcribe_flow(fast_poll, monkeypatch):
    sess, calls = make_session_class(
        FakeResp(payload={"done": False, "id": "op-1"}),
        FakeResp(payload={"done": True, "response": {"chunks": [
            {"alternatives": [{"text": "привет"}]},
            {"alternatives": [{"text": "друг"}]},
        ]}}),
    )
    monkeypatch.setattr(yx.aiohttp, "ClientSession", sess)
    assert await _provider().transcribe(AUDIO) == "привет друг"
    assert calls[0]["url"] == yx.TRANSCRIBE_URL
    assert calls[0]["json"]["audio"]["content"]  # base64 аудио
    assert calls[1]["url"] == yx.OPERATION_URL.format(operation_id="op-1")


async def test_yandex_operation_error(fast_poll, monkeypatch):
    sess, _ = make_session_class(
        FakeResp(payload={"done": False, "id": "op-2"}),
        FakeResp(payload={"done": True, "error": {"code": 13, "message": "internal"}}),
    )
    monkeypatch.setattr(yx.aiohttp, "ClientSession", sess)
    with pytest.raises(AIProviderError, match="internal"):
        await _provider().transcribe(AUDIO)


async def test_yandex_empty_audio(fast_poll, monkeypatch):
    sess, calls = make_session_class()
    monkeypatch.setattr(yx.aiohttp, "ClientSession", sess)
    with pytest.raises(AIProviderError, match="пустой аудиофайл"):
        await _provider().transcribe(b"")
    assert calls == []


def test_yandex_auth_and_model_uri():
    iam = YandexGPTProvider(iam_token="iam", folder_id="fld")
    assert iam.is_configured and iam._auth_header() == "Bearer iam"
    assert iam._model_uri() == "gpt://fld/yandexgpt/rc"
    lite = YandexGPTProvider(api_key="k", folder_id="f", model="yandexgpt-lite")
    assert lite._model_uri() == "gpt://f/yandexgpt-lite/latest"
    assert not YandexGPTProvider(api_key="", iam_token="", folder_id="").is_configured


async def test_yandex_not_configured():
    empty = YandexGPTProvider(api_key="", iam_token="", folder_id="")
    with pytest.raises(AIProviderError) as ei:
        await empty.ask("в")
    assert str(ei.value) == yx.NOT_CONFIGURED_HINT
    with pytest.raises(AIProviderError) as ei2:
        await empty.transcribe(AUDIO)
    assert str(ei2.value) == yx.NOT_CONFIGURED_HINT


def test_yandex_poll_defaults_restored():
    # после fast_poll-тестов константы опроса/ретраев должны остаться исходными
    assert yx.POLL_INTERVAL == 3.0
    assert yx.RETRY_BACKOFF == 1.5
    assert yx.RETRY_ATTEMPTS == 3


# ---------- фасад ai ----------

class _FakeOk(AIProvider):
    name = "fake"

    @classmethod
    def from_config(cls):
        return cls()

    @property
    def is_configured(self):
        return True

    async def ask(self, question):
        return f"ответ на: {question}"

    async def transcribe(self, audio):
        return ""


class _FakeFail(_FakeOk):
    async def ask(self, question):
        raise AIProviderError("Ошибка X")


async def test_facade_ask_ai_delegates(monkeypatch):
    monkeypatch.setattr(ai_facade, "get_ai_provider", lambda: _FakeOk())
    assert await ai_facade.ask_ai("тест") == "ответ на: тест"


async def test_facade_error_becomes_hint(monkeypatch):
    # Ошибка после исчерпания ретраев — пользователю фраза-заглушка, не техдетали
    monkeypatch.setattr(ai_facade, "get_ai_provider", lambda: _FakeFail())
    monkeypatch.setattr(rs, "RETRY_BACKOFF", 0)  # без пауз между повторами
    try:
        assert await ai_facade.ask_ai("тест") in AI_FALLBACK_PHRASES
    finally:
        rs.reset_breaker()  # не засорять общий брейкер другим тестам


class _FakeText(_FakeOk):
    """Провайдер, возвращающий заданный «успешный» ответ (тесты фасада)."""

    def __init__(self, text):
        self._text = text

    async def ask(self, question):
        return self._text


async def test_facade_degenerated_answer_becomes_fallback(monkeypatch):
    # Деградация модели (маркер секции повторяется, секции пустые) — менеджеру
    # фраза-заглушка, мусор в чат не попадает (кейс «плохого ключа»).
    garbage = (
        "<b>💡 Вывод:</b> Привет, менеджер! Начнем работу.\n"
        "<b>🗣 Что сказать клиенту:</b> Мы готовы помочь.\n"
        "<b>❓ Какие вопросы уточнить:</b> Какая задача?\n"
    ) + "<b>💡 Вывод:</b> \n" * 5
    monkeypatch.setattr(ai_facade, "get_ai_provider", lambda: _FakeText(garbage))
    assert await ai_facade.ask_ai("тест") in AI_FALLBACK_PHRASES


async def test_facade_leaked_error_text_becomes_fallback(monkeypatch):
    # Протёкший технический текст (невалидный ключ бэкенда) — тоже заглушка.
    monkeypatch.setattr(
        ai_facade, "get_ai_provider", lambda: _FakeText("Unauthorized: invalid API key")
    )
    assert await ai_facade.ask_ai("тест") in AI_FALLBACK_PHRASES


async def test_facade_marker_only_answer_becomes_fallback(monkeypatch):
    # Ответ из одного пустого маркера без содержимого — заглушка.
    monkeypatch.setattr(
        ai_facade, "get_ai_provider", lambda: _FakeText("<b>💡 Вывод:</b>")
    )
    assert await ai_facade.ask_ai("тест") in AI_FALLBACK_PHRASES


async def test_facade_normal_answer_passthrough(monkeypatch):
    # Штатный ответ (один маркер + содержимое секций) проходит без замен.
    ok = (
        "<b>💡 Вывод:</b> Для дальности берём 6S4P.\n"
        "<b>🗣 Что сказать клиенту:</b> Наша батарея — про километры, не про рывок."
    )
    monkeypatch.setattr(ai_facade, "get_ai_provider", lambda: _FakeText(ok))
    assert await ai_facade.ask_ai("тест") == ok


async def test_cloudflare_error_payload_in_200_body(monkeypatch):
    # HTTP 200, но прокси отдал {"ok": false, "error": ...} (старая/другая
    # версия прокси) — провайдер бросает AIProviderError: далее сработают
    # retry/breaker и менеджер получит фразу-заглушку.
    sess, _ = make_session_class(FakeResp(payload={"ok": False, "error": "Unauthorized"}))
    monkeypatch.setattr(cf.aiohttp, "ClientSession", sess)
    p = CloudflareAIProvider(base_url="https://proxy.example", token="tok")
    with pytest.raises(AIProviderError):
        await p.ask("вопрос")