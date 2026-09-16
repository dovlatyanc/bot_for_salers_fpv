"""Проверка services/ai_providers: фабрика, Cloudflare- и Yandex-провайдеры, фасад ai.ask_ai.

HTTP не используется: aiohttp подменяется фейковой сессией.

Запуск: .venv\\Scripts\\python.exe _verify_ai_providers.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import aiohttp  # noqa: E402

import ai.ai as ai_facade  # noqa: E402
import bot  # noqa: E402  — проверка, что вся сборка (включая handlers.voice) импортируется
import services.ai_providers.cloudflare as cf  # noqa: E402
import services.ai_providers.yandex as yx  # noqa: E402
from services.ai_providers import (  # noqa: E402
    AIProvider,
    AIProviderError,
    CloudflareAIProvider,
    YandexGPTProvider,
    get_ai_provider,
)
from services.ai_providers.factory import PROVIDER_REGISTRY  # noqa: E402

AUDIO = b"fake-audio-bytes"


# ---------- Подмена aiohttp для HTTP-тестов без сети ----------

FAKE_QUEUE: list = []


def _set_fake(*resps):
    """Очередь ответов: каждый вызов post()/get() забирает следующий элемент."""
    FAKE_QUEUE.clear()
    FAKE_QUEUE.extend(resps)


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _RespCtx:
    async def __aenter__(self):
        if not FAKE_QUEUE:
            raise AssertionError("Нет заготовленного HTTP-ответа для этого запроса")
        resp = FAKE_QUEUE.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def __aexit__(self, *args):
        return False


class FakeSession:
    """Подмена aiohttp.ClientSession: пишет запросы в requests, отдаёт ответы из очереди."""

    requests = []
    last_request = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _record(self, url, **kwargs):
        FakeSession.requests.append({"url": url, **kwargs})
        FakeSession.last_request = FakeSession.requests[-1]

    def post(self, url, data=None, json=None, headers=None):
        self._record(url, data=data, json=json, headers=headers)
        return _RespCtx()

    def get(self, url, headers=None):
        self._record(url, headers=headers)
        return _RespCtx()

    def request(self, method, url, headers=None, json=None, timeout=None):
        """Форма вызова session.request(...) — так ходит YandexGPTProvider."""
        self._record(url, method=method, data=None, json=json,
                     headers=headers, timeout=timeout)
        return _RespCtx()


# ---------- Фейковые провайдеры для проверки фасада ----------

class FakeOkProvider(AIProvider):
    name = "fake"

    @classmethod
    def from_config(cls):
        return cls()

    async def ask(self, question):
        return f"ответ на: {question}"

    async def transcribe(self, audio):
        return ""


class FakeFailProvider(FakeOkProvider):
    async def ask(self, question):
        raise AIProviderError("Ошибка X", status=500, body="b")


# ---------- Тесты ----------

async def _run_tests():
    # --- Базовый контракт ---
    assert issubclass(CloudflareAIProvider, AIProvider)
    try:
        AIProvider()
        raise AssertionError("AIProvider не должен инстанцироваться напрямую")
    except TypeError:
        pass
    print("[OK] base: ABC-контракт (инстанцировать абстракцию нельзя)")

    # --- Фабрика ---
    p = get_ai_provider("cloudflare")
    assert isinstance(p, CloudflareAIProvider)
    assert p.name == "cloudflare"

    try:
        get_ai_provider("no-such-provider")
        raise AssertionError("ожидали SystemExit для неизвестного провайдера")
    except SystemExit as e:
        assert "no-such-provider" in str(e)

    s1, s2 = get_ai_provider(), get_ai_provider()
    assert s1 is s2, "get_ai_provider() без аргумента должен возвращать синглтон"
    assert s1.name == "cloudflare"
    assert set(PROVIDER_REGISTRY) == {"cloudflare", "yandex"}, PROVIDER_REGISTRY
    print("[OK] factory: реестр (cloudflare, yandex), синглтон, SystemExit")

    # --- Cloudflare-провайдер с подменой HTTP ---
    real_client_session = cf.aiohttp.ClientSession
    cf.aiohttp.ClientSession = FakeSession
    try:
        provider = CloudflareAIProvider(base_url="https://proxy.test", token="secret")

        # ask: успешный ответ
        _set_fake(FakeResp(payload={"answer": "Отвечаю"}))
        answer = await provider.ask("вопрос")
        assert answer == "Отвечаю"
        req = FakeSession.last_request
        assert req["url"] == "https://proxy.test"
        assert req["json"] == {"question": "вопрос", "source": "python-telegram-bot"}
        assert req["headers"]["Authorization"] == "Bearer secret"
        assert req["headers"]["Content-Type"] == "application/json"
        print("[OK] ask(): успех, payload и заголовки корректны")

        # ask: HTTP 500
        _set_fake(FakeResp(status=500, text="boom"))
        try:
            await provider.ask("вопрос")
            raise AssertionError("ожидали AIProviderError при HTTP 500")
        except AIProviderError as e:
            assert e.status == 500 and e.body == "boom"
            assert str(e).startswith("Cloudflare AI вернул ошибку 500")
        print("[OK] ask(): HTTP-ошибка -> AIProviderError со статусом и телом")

        # ask: пустой ответ
        _set_fake(FakeResp(payload={"answer": ""}))
        assert await provider.ask("вопрос") == "AI вернул пустой ответ."
        print("[OK] ask(): пустой ответ -> сообщение-заглушка")

        # ask: сетевая ошибка
        _set_fake(aiohttp.ClientConnectionError("connection refused"))
        try:
            await provider.ask("вопрос")
            raise AssertionError("ожидали AIProviderError при сетевой ошибке")
        except AIProviderError as e:
            assert str(e).startswith("Ошибка запроса к AI:")
        print("[OK] ask(): сетевая ошибка -> AIProviderError")

        # transcribe: успех
        _set_fake(FakeResp(payload={"text": "  привет  "}))
        text = await provider.transcribe(AUDIO)
        assert text == "привет"
        req = FakeSession.last_request
        assert req["url"] == "https://proxy.test/transcribe"
        assert req["data"] == AUDIO
        assert req["headers"]["Content-Type"] == "application/octet-stream"
        assert req["headers"]["Authorization"] == "Bearer secret"
        print("[OK] transcribe(): успех, /transcribe + octet-stream")

        # transcribe: HTTP 400
        _set_fake(FakeResp(status=400, text="bad request"))
        try:
            await provider.transcribe(AUDIO)
            raise AssertionError("ожидали AIProviderError при HTTP 400")
        except AIProviderError as e:
            assert e.status == 400 and e.body == "bad request"
        print("[OK] transcribe(): HTTP-ошибка -> AIProviderError со статусом и телом")

        # провайдер не сконфигурирован
        empty = CloudflareAIProvider(base_url="", token="")
        assert await empty.ask("вопрос") == cf.NOT_CONFIGURED_HINT
        try:
            await empty.transcribe(AUDIO)
            raise AssertionError("ожидали AIProviderError для неконфигурированного transcribe")
        except AIProviderError as e:
            assert e.status is None
        print("[OK] неконфигурированный прокси: ask() -> подсказка, transcribe() -> ошибка")
    finally:
        cf.aiohttp.ClientSession = real_client_session

    # --- Yandex-провайдер с подменой HTTP ---
    yx.POLL_INTERVAL = 0.001
    yx.RETRY_BACKOFF = 0.001
    yx_provider = YandexGPTProvider(api_key="ykey", folder_id="fld", model="yandexgpt")
    real_client_session = yx.aiohttp.ClientSession
    yx.aiohttp.ClientSession = FakeSession
    try:
        FakeSession.requests.clear()

        # ask: успех — проверяем промт, modelUri (/rc) и заголовки
        _set_fake(FakeResp(payload={"result": {"alternatives": [
            {"message": {"role": "assistant", "text": "Предложите 6S2P"}},
        ]}}))
        answer = await yx_provider.ask("Что предложить клиенту?")
        assert answer == "Предложите 6S2P"
        req = FakeSession.requests[0]
        assert req["url"] == yx.COMPLETION_URL
        assert req["json"]["modelUri"] == "gpt://fld/yandexgpt/rc"
        assert req["json"]["messages"][0]["role"] == "system"
        prompt_text = req["json"]["messages"][0]["text"]
        assert "АКБ" in prompt_text and "дрон" in prompt_text.lower()
        assert req["json"]["messages"][1] == {
            "role": "user",
            "text": "Что предложить клиенту?",
        }
        assert req["headers"]["Authorization"] == "Api-Key ykey"
        print("[OK] yandex ask(): system-промт, modelUri (/rc) и заголовки в запросе")

        # ask: 429 -> 429 -> успех (retry с backoff)
        n = len(FakeSession.requests)
        _set_fake(
            FakeResp(status=429, text="quota"),
            FakeResp(status=429, text="quota"),
            FakeResp(payload={"result": {"alternatives": [
                {"message": {"text": "ОК"}},
            ]}}),
        )
        assert await yx_provider.ask("вопрос") == "ОК"
        assert len(FakeSession.requests) - n == 3
        print("[OK] yandex ask(): 429 ретраится (3 запроса) и завершается успехом")

        # ask: HTTP 400 — клиентская ошибка, без retry
        n = len(FakeSession.requests)
        _set_fake(FakeResp(status=400, text="bad request"))
        try:
            await yx_provider.ask("вопрос")
            raise AssertionError("ожидали AIProviderError при HTTP 400")
        except AIProviderError as e:
            assert e.status == 400 and e.body == "bad request"
        assert len(FakeSession.requests) - n == 1
        print("[OK] yandex ask(): HTTP 400 -> AIProviderError без retry")

        # ask: неожиданная структура ответа
        _set_fake(FakeResp(payload={"result": {}}))
        try:
            await yx_provider.ask("вопрос")
            raise AssertionError("ожидали AIProviderError при неожиданном ответе")
        except AIProviderError as e:
            assert "неожиданный ответ" in str(e)
        print("[OK] yandex ask(): неожиданный ответ -> AIProviderError")

        # transcribe: операция -> опрос -> текст
        n = len(FakeSession.requests)
        _set_fake(
            FakeResp(payload={"done": False, "id": "op-1"}),
            FakeResp(payload={"done": True, "response": {"chunks": [
                {"alternatives": [{"text": "привет"}]},
                {"alternatives": [{"text": "друг"}]},
            ]}}),
        )
        text = await yx_provider.transcribe(AUDIO)
        assert text == "привет друг"
        assert FakeSession.requests[n]["url"] == yx.TRANSCRIBE_URL
        assert FakeSession.requests[n]["json"]["audio"]["content"]
        assert FakeSession.requests[n + 1]["url"] == yx.OPERATION_URL.format(
            operation_id="op-1"
        )
        print("[OK] yandex transcribe(): SpeechKit -> распознанный текст")

        # transcribe: операция завершилась ошибкой
        _set_fake(
            FakeResp(payload={"done": False, "id": "op-2"}),
            FakeResp(payload={"done": True, "error": {"code": 13, "message": "internal"}}),
        )
        try:
            await yx_provider.transcribe(AUDIO)
            raise AssertionError("ожидали AIProviderError при ошибке операции")
        except AIProviderError as e:
            assert "internal" in str(e)
        print("[OK] yandex transcribe(): ошибка операции -> AIProviderError")

        # IAM-токен как альтернатива Api-Key + модель по умолчанию
        iam = YandexGPTProvider(iam_token="iam", folder_id="fld")
        assert iam.is_configured and iam._auth_header() == "Bearer iam"
        assert iam._model_uri() == "gpt://fld/yandexgpt/rc"
        lite = YandexGPTProvider(api_key="k", folder_id="f", model="yandexgpt-lite")
        assert lite._model_uri() == "gpt://f/yandexgpt-lite/latest"
        print("[OK] yandex: IAM-токен (Bearer), /rc для yandexgpt, /latest для lite")

        # неконфигурированный провайдер: оба метода бросают AIProviderError с подсказкой
        empty = YandexGPTProvider(api_key="", iam_token="", folder_id="")
        assert not empty.is_configured
        for coro in (empty.ask("вопрос"), empty.transcribe(AUDIO)):
            try:
                await coro
                raise AssertionError("ожидали AIProviderError для неконфигурированного провайдера")
            except AIProviderError as e:
                assert str(e) == yx.NOT_CONFIGURED_HINT
        print("[OK] yandex неконфигурирован: ask()/transcribe() -> AIProviderError с подсказкой")
    finally:
        yx.aiohttp.ClientSession = real_client_session
        yx.POLL_INTERVAL = 3.0
        yx.RETRY_BACKOFF = 1.5

    # --- Фасад ai.ask_ai ---
    orig = ai_facade.get_ai_provider
    try:
        ai_facade.get_ai_provider = lambda: FakeOkProvider()
        assert await ai_facade.ask_ai("тест") == "ответ на: тест"

        ai_facade.get_ai_provider = lambda: FakeFailProvider()
        assert await ai_facade.ask_ai("тест") == "Ошибка X"
        print("[OK] фасад ask_ai(): делегирует провайдеру и превращает ошибку в текст")
    finally:
        ai_facade.get_ai_provider = orig

    # --- from_config читает конфиг приложения ---
    cfg_provider = CloudflareAIProvider.from_config()
    assert isinstance(cfg_provider, CloudflareAIProvider)
    print("[OK] from_config(): провайдер создаётся из config.py/.env")

    print("\nВсе проверки services/ai_providers пройдены.")


if __name__ == "__main__":
    asyncio.run(_run_tests())

