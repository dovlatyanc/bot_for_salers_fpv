"""
Провайдер «Cloudflare AI Worker» — текущий бэкенд (проект fpv-ai-proxy).

API прокси:
  POST {AI_PROXY_URL}             JSON {"question", "source"} -> {"answer": str}
  POST {AI_PROXY_URL}/transcribe  raw audio (octet-stream)     -> {"text": str}

Аутентификация: заголовок Authorization: Bearer <AI_PROXY_TOKEN> (если токен задан).
Поведение и тексты сообщений повторяют прежнюю логику ai/ai.py и handlers/voice.py.
"""

import aiohttp

from config import AI_PROXY_TOKEN, AI_PROXY_URL, system_logger
from services.ai_providers.base import AIProvider, AIProviderError

#: Метка источника, передаваемая прокси вместе с вопросом.
SOURCE = "python-telegram-bot"
#: Таймауты (сек): вопрос к ассистенту / распознавание речи (голосовые до 20 МБ).
ASK_TIMEOUT = 90
TRANSCRIBE_TIMEOUT = 300
#: Подсказка, когда прокси не сконфигурирован (возвращается вместо ответа).
NOT_CONFIGURED_HINT = (
    "AI-ассистент ещё не подключён. Заполните AI_PROXY_URL и AI_PROXY_TOKEN в .env"
)


class CloudflareAIProvider(AIProvider):
    """Реализация AIProvider поверх HTTP-прокси на Cloudflare Workers."""

    name = "cloudflare"

    def __init__(self, base_url: str = "", token: str = ""):
        self._base_url = base_url.strip().rstrip("/")
        self._token = token.strip()

    @classmethod
    def from_config(cls) -> "CloudflareAIProvider":
        return cls(base_url=AI_PROXY_URL, token=AI_PROXY_TOKEN)

    @property
    def is_configured(self) -> bool:
        """True, если задан адрес прокси (токен опционален)."""
        return bool(self._base_url)

    def _headers(self, content_type: str) -> dict[str, str]:
        headers = {"Content-Type": content_type}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def ask(self, question: str) -> str:
        if not self.is_configured:
            system_logger.warning("AI_PROXY_URL not configured")
            return NOT_CONFIGURED_HINT

        payload = {"question": question, "source": SOURCE}
        timeout = aiohttp.ClientTimeout(total=ASK_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self._base_url,
                    json=payload,
                    headers=self._headers("application/json"),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        system_logger.error(f"AI proxy returned {resp.status}: {body[:500]}")
                        raise AIProviderError(
                            f"Cloudflare AI вернул ошибку {resp.status}:\n{body[:500]}",
                            status=resp.status,
                            body=body,
                        )
                    data = await resp.json()
                    # Явная ошибка в теле при HTTP 200 (старая/другая версия
                    # прокси отвечает {"ok": false, "error": ...}) — тоже сбой:
                    # бросаем исключение, чтобы сработали retry/breaker, и
                    # менеджер получил фразу-заглушку, а не текст ошибки.
                    if data.get("ok") is False or data.get("error"):
                        err = str(data.get("error") or "proxy error").strip()[:500]
                        system_logger.error(
                            f"AI proxy reported error with HTTP 200: {err}"
                        )
                        raise AIProviderError(
                            f"AI proxy reported error: {err}", status=resp.status
                        )
                    answer = data.get("answer")
                    if not answer:
                        system_logger.warning("AI returned empty answer")
                        return "AI вернул пустой ответ."
                    return answer
        except AIProviderError:
            raise
        except Exception as e:
            # Сетевые ошибки, таймауты, невалидный JSON — всё это «ошибка запроса к AI».
            # CancelledError не перехватывается (BaseException в Python 3.8+).
            system_logger.exception("AI request failed")
            raise AIProviderError(f"Ошибка запроса к AI: {e}") from e

    async def transcribe(self, audio: bytes) -> str:
        if not self.is_configured:
            raise AIProviderError("Распознавание речи недоступно: не заполнен AI_PROXY_URL в .env")

        timeout = aiohttp.ClientTimeout(total=TRANSCRIBE_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self._base_url}/transcribe",
                    data=audio,
                    headers=self._headers("application/octet-stream"),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        system_logger.error(
                            f"AI proxy /transcribe returned {resp.status}: {body[:500]}"
                        )
                        raise AIProviderError(
                            f"Ошибка распознавания (HTTP {resp.status})",
                            status=resp.status,
                            body=body,
                        )
                    data = await resp.json()
                    return (data.get("text") or "").strip()
        except AIProviderError:
            raise
        except Exception as e:
            system_logger.exception("AI transcribe request failed")
            raise AIProviderError(f"Ошибка запроса на распознавание: {e}") from e
