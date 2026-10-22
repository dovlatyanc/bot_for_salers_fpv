"""
Абстракция AI-провайдеров: запросы к ассистенту + распознавание речи.

Публичный API пакета:
    get_ai_provider()     — провайдер, выбранный переменной AI_PROVIDER в .env;
    get_resilient_provider() — тот же провайдер + retry + circuit breaker
                            (рантайм-надёжность, см. resilience.py);
    AIProvider            — интерфейс провайдера (ask / transcribe);
    AIProviderError       — ошибка взаимодействия с провайдером;
    ResilientAIProvider / CircuitBreaker / get_breaker / reset_breaker
                          — обёртка отказоустойчивости и её состояние;
    AI_FALLBACK_PHRASES / ai_fallback_phrase()
                          — пул дружелюбных фраз-заглушек деградации и выбор
                            из него (аудит п. 2.3, см. prompts.py);
    CloudflareAIProvider  — бэкенд Cloudflare AI Worker;
    YandexGPTProvider     — бэкенд YandexGPT + Yandex SpeechKit.

Добавление нового провайдера — см. docstring factory.py.
"""

from .base import AIProvider, AIProviderError
from .cloudflare import CloudflareAIProvider
from .factory import get_ai_provider
from .resilience import (
    AI_FALLBACK_PHRASES,
    CircuitBreaker,
    ResilientAIProvider,
    ai_fallback_phrase,
    get_breaker,
    get_resilient_provider,
    reset_breaker,
)
from .yandex import YandexGPTProvider

__all__ = [
    "AIProvider",
    "AIProviderError",
    "AI_FALLBACK_PHRASES",
    "CircuitBreaker",
    "CloudflareAIProvider",
    "ResilientAIProvider",
    "YandexGPTProvider",
    "ai_fallback_phrase",
    "get_ai_provider",
    "get_breaker",
    "get_resilient_provider",
    "reset_breaker",
]
