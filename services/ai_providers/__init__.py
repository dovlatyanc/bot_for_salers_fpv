"""
Абстракция AI-провайдеров: запросы к ассистенту + распознавание речи.

Публичный API пакета:
    get_ai_provider()     — провайдер, выбранный переменной AI_PROVIDER в .env;
    AIProvider            — интерфейс провайдера (ask / transcribe);
    AIProviderError       — ошибка взаимодействия с провайдером;
    CloudflareAIProvider  — бэкенд Cloudflare AI Worker;
    YandexGPTProvider     — бэкенд YandexGPT + Yandex SpeechKit.

Добавление нового провайдера — см. docstring factory.py.
"""

from .base import AIProvider, AIProviderError
from .cloudflare import CloudflareAIProvider
from .factory import get_ai_provider
from .yandex import YandexGPTProvider

__all__ = [
    "AIProvider",
    "AIProviderError",
    "CloudflareAIProvider",
    "YandexGPTProvider",
    "get_ai_provider",
]
