"""
Провайдер «YandexGPT» — бэкенд Yandex Cloud (LLM + SpeechKit).

API (REST; аутентификация: Api-Key или IAM-токен):

  Вопрос к ассистенту (YandexGPT, Foundation Models):
    POST https://llm.api.cloud.yandex.net/foundationModels/v1/completion
    {"modelUri": "gpt://<folder_id>/<model>/<version>",
     "completionOptions": {"stream": false, "temperature": ..., "maxTokens": ...},
     "messages": [{"role": "system", "text": <промт из prompts.py>},
                  {"role": "user", "text": <вопрос>}]}
    -> {"result": {"alternatives": [{"message": {"role": "assistant", "text": "..."}}]}}

  Распознавание речи (SpeechKit v2, длинная асинхронная операция):
    POST https://transcribe.api.cloud.yandex.net/speech/stt/v2/longRunningRecognize
    {"config": {"specification": {"languageCode": "ru-RU",
                                  "audioEncoding": "OGG_OPUS",
                                  "sampleRateHertz": 48000}},
     "audio": {"content": "<base64>"}}
    -> {"id": "<operation_id>", "done": false}
    Затем опрос GET https://operation.api.cloud.yandex.net/operations/<id>
    -> {"done": true, "response": {"chunks": [{"alternatives": [{"text": "..."}]}]}}
    или {"done": true, "error": {"code": ..., "message": "..."}}

Голосовые сообщения Telegram — OGG/Opus, 48 кГц, моно: SpeechKit этот формат
поддерживает, пересжатие не требуется. Промт ассистента (коуч-продажник: АКБ
и дроны) живёт отдельно — services/ai_providers/prompts.py.

Особенности:
  • modelUri для yandexgpt / yandexgpt-32k требует версию /rc, а не /latest;
    для yandexgpt-lite допустим /latest. Учитывается в _model_uri().
  • 429/5xx, таймауты и сетевые ошибки — retry внутри _request_json
    (внешняя обёртка resilience.py не дублирует повторы, retries_internally).
  • При is_configured == False ask()/transcribe() бросают AIProviderError
    с текстом NOT_CONFIGURED_HINT — вызывающий код сам решает, показывать ли.
"""

import asyncio
import base64
import time

import aiohttp

from config import (
    YANDEX_API_KEY,
    YANDEX_FOLDER_ID,
    YANDEX_IAM_TOKEN,
    YANDEX_MODEL,
    system_logger,
)
from services.ai_providers.base import AIProvider, AIProviderError
from services.ai_providers.prompts import YANDEX_SYSTEM_PROMPT

#: REST-эндпоинты Yandex Cloud (Foundation Models / SpeechKit v2 / Operations).
COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
TRANSCRIBE_URL = (
    "https://transcribe.api.cloud.yandex.net/speech/stt/v2/longRunningRecognize"
)
OPERATION_URL = "https://operation.api.cloud.yandex.net/operations/{operation_id}"

#: Таймауты (сек): вопрос к ассистенту / распознавание речи (голосовые до 20 МБ).
ASK_TIMEOUT = 120
TRANSCRIBE_TIMEOUT = 300
#: Интервал опроса статуса операции распознавания (сек) и максимум попыток.
POLL_INTERVAL = 3.0
POLL_MAX_INTERVAL = 10.0
POLL_MAX_ATTEMPTS = 100
#: Retry для 429/5xx: количество попыток и базовый backoff (сек).
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.5

#: Подсказка, когда провайдер не сконфигурирован (бросается как AIProviderError).
NOT_CONFIGURED_HINT = (
    "YandexGPT ещё не подключён. Заполните YANDEX_API_KEY и YANDEX_FOLDER_ID в .env"
)


class YandexGPTProvider(AIProvider):
    """Реализация AIProvider поверх YandexGPT + Yandex SpeechKit."""

    name = "yandex"

    #: 429/5xx/таймауты/сетевые ошибки ретраятся внутри _request_json —
    #: внешняя обёртка resilience.py повторы не дублирует.
    retries_internally = True

    #: Параметры генерации. Для коуча-продажника 0.3–0.4 — меньше «фантазий»
    #: по цифрам и порогам; выше 0.7 — сильнее разброс.
    temperature = 0.35
    max_tokens = 2000

    #: Параметры распознавания: Telegram voice — OGG/Opus, 48 кГц, моно.
    stt_language = "ru-RU"
    stt_sample_rate = 48000
    stt_encoding = "OGG_OPUS"

    def __init__(
        self,
        api_key: str = "",
        iam_token: str = "",
        folder_id: str = "",
        model: str = "",
    ):
        self._api_key = api_key.strip()
        self._iam_token = iam_token.strip()
        self._folder_id = folder_id.strip()
        self._model = model.strip() or "yandexgpt"

    @classmethod
    def from_config(cls) -> "YandexGPTProvider":
        return cls(
            api_key=YANDEX_API_KEY,
            iam_token=YANDEX_IAM_TOKEN,
            folder_id=YANDEX_FOLDER_ID,
            model=YANDEX_MODEL,
        )

    @property
    def is_configured(self) -> bool:
        """True, если задан каталог и ключ (или IAM-токен)."""
        return bool(self._folder_id and (self._api_key or self._iam_token))

    def _auth_header(self) -> str:
        """Заголовок авторизации: Api-Key приоритетнее IAM-токена."""
        if self._api_key:
            return f"Api-Key {self._api_key}"
        return f"Bearer {self._iam_token}"

    def _model_uri(self) -> str:
        """modelUri для Foundation Models.

        yandexgpt / yandexgpt-32k требуют версию /rc (или конкретную дату),
        /latest для них не поддерживается и даёт 400. yandexgpt-lite
        допускает /latest.
        """
        if self._model in ("yandexgpt", "yandexgpt-32k"):
            version = "rc"
        else:
            version = "latest"
        return f"gpt://{self._folder_id}/{self._model}/{version}"

    def _ensure_configured(self) -> None:
        if not self.is_configured:
            raise AIProviderError(NOT_CONFIGURED_HINT)

    @staticmethod
    async def _request_json(
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        json_body: dict | None = None,
        timeout: float,
    ) -> dict:
        """HTTP-запрос с retry на 429/5xx и возвратом JSON."""
        last_error: Exception | None = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                async with session.request(
                    method, url, headers=headers, json=json_body,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if response.status in (429, 500, 502, 503, 504):
                        text = await response.text()
                        last_error = AIProviderError(
                            f"Yandex Cloud {response.status}: {text[:300]}",
                            status=response.status,
                            body=text,
                        )
                        if attempt < RETRY_ATTEMPTS:
                            await asyncio.sleep(RETRY_BACKOFF * attempt)
                            continue
                        raise last_error
                    if response.status >= 400:
                        text = await response.text()
                        raise AIProviderError(
                            f"Yandex Cloud {response.status}: {text[:300]}",
                            status=response.status,
                            body=text,
                        )
                    return await response.json()
            except asyncio.TimeoutError as e:
                last_error = e
                if attempt < RETRY_ATTEMPTS:
                    await asyncio.sleep(RETRY_BACKOFF * attempt)
                    continue
                raise AIProviderError(f"Yandex Cloud timeout: {timeout} с") from e
            except (aiohttp.ClientError, OSError) as e:
                # Сетевые сбои (DNS, обрыв соединения, SSL) — тоже переходящие:
                # без этой ветки они утекали бы из ask()/transcribe() сырыми
                # исключениями aiohttp мимо контракта AIProviderError.
                last_error = e
                if attempt < RETRY_ATTEMPTS:
                    await asyncio.sleep(RETRY_BACKOFF * attempt)
                    continue
                raise AIProviderError(f"Yandex Cloud network: {e}") from e
        raise AIProviderError(str(last_error) if last_error else "Yandex Cloud error")

    async def ask(self, question: str) -> str:
        """Запрос к YandexGPT. Возвращает текст ответа ассистента."""
        self._ensure_configured()
        headers = {
            "Authorization": self._auth_header(),
            "Content-Type": "application/json",
        }
        body = {
            "modelUri": self._model_uri(),
            "completionOptions": {
                "stream": False,
                "temperature": self.temperature,
                "maxTokens": str(self.max_tokens),
            },
            "messages": [
                {"role": "system", "text": YANDEX_SYSTEM_PROMPT},
                {"role": "user", "text": question},
            ],
        }
        started = time.monotonic()
        async with aiohttp.ClientSession() as session:
            data = await self._request_json(
                session, "POST", COMPLETION_URL,
                headers=headers, json_body=body, timeout=ASK_TIMEOUT,
            )
        try:
            text = data["result"]["alternatives"][0]["message"]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise AIProviderError(f"YandexGPT: неожиданный ответ: {data}") from e
        system_logger.info(
            f"yandexgpt ask: {len(question)} симв. → {len(text)} симв. "
            f"за {time.monotonic() - started:.1f} с"
        )
        return text

    async def transcribe(self, audio_bytes: bytes) -> str:
        """Распознавание OGG/Opus через SpeechKit v2 longRunningRecognize."""
        self._ensure_configured()
        if not audio_bytes:
            raise AIProviderError("SpeechKit: пустой аудиофайл")
        headers = {
            "Authorization": self._auth_header(),
            "Content-Type": "application/json",
        }
        body = {
            "config": {
                "specification": {
                    "languageCode": self.stt_language,
                    "audioEncoding": self.stt_encoding,
                    "sampleRateHertz": self.stt_sample_rate,
                }
            },
            "audio": {"content": base64.b64encode(audio_bytes).decode("ascii")},
        }
        started = time.monotonic()
        async with aiohttp.ClientSession() as session:
            data = await self._request_json(
                session, "POST", TRANSCRIBE_URL,
                headers=headers, json_body=body, timeout=ASK_TIMEOUT,
            )
            operation_id = data.get("id")
            if not operation_id:
                raise AIProviderError(f"SpeechKit: нет id операции: {data}")
            result = await self._poll_operation(session, operation_id)
        text = self._extract_text(result)
        system_logger.info(
            f"speechkit transcribe: {len(audio_bytes)} байт → {len(text)} симв. "
            f"за {time.monotonic() - started:.1f} с"
        )
        return text

    async def _poll_operation(
        self, session: aiohttp.ClientSession, operation_id: str,
    ) -> dict:
        """Опрос статуса longRunningRecognize до done=True.

        Интервал растёт с POLL_INTERVAL до POLL_MAX_INTERVAL, чтобы не
        долбить Operations API и не выходить за квоты.
        """
        headers = {"Authorization": self._auth_header()}
        url = OPERATION_URL.format(operation_id=operation_id)
        interval = POLL_INTERVAL
        for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
            data = await self._request_json(
                session, "GET", url, headers=headers, timeout=ASK_TIMEOUT,
            )
            if data.get("done"):
                if "error" in data:
                    err = data["error"]
                    raise AIProviderError(
                        f"SpeechKit: {err.get('code')} {err.get('message')}"
                    )
                return data
            if attempt < POLL_MAX_ATTEMPTS:
                await asyncio.sleep(interval)
                interval = min(interval * 1.5, POLL_MAX_INTERVAL)
        raise AIProviderError(
            f"SpeechKit: операция {operation_id} не завершилась "
            f"за {POLL_MAX_ATTEMPTS} опросов"
        )

    @staticmethod
    def _extract_text(operation_result: dict) -> str:
        """Собирает текст из chunks[].alternatives[0].text."""
        try:
            chunks = operation_result["response"]["chunks"]
        except (KeyError, TypeError) as e:
            raise AIProviderError(
                f"SpeechKit: неожиданный ответ: {operation_result}"
            ) from e
        parts = []
        for chunk in chunks:
            alternatives = chunk.get("alternatives") or []
            if alternatives:
                text = alternatives[0].get("text")
                if text:
                    parts.append(text)
        return " ".join(parts).strip()