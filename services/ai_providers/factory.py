"""
Фабрика AI-провайдеров: выбор бэкенда через переменную окружения AI_PROVIDER.

    AI_PROVIDER=cloudflare   # Cloudflare AI Worker (по умолчанию)
    AI_PROVIDER=yandex       # YandexGPT + Yandex SpeechKit (промт — prompts.py)

Как добавить нового провайдера:
  1. Создать services/ai_providers/<name>.py — класс <Name>Provider(AIProvider)
     с реализацией ask()/transcribe() и from_config(), читающим свои .env-переменные
     (образцы — cloudflare.py и yandex.py);
  2. Зарегистрировать класс в PROVIDER_REGISTRY ниже;
  3. Переключить AI_PROVIDER=<name> в .env.

Бизнес-логика бота (handlers/, ai/) при этом не меняется.
"""

from config import AI_PROVIDER
from services.ai_providers.base import AIProvider
from services.ai_providers.cloudflare import CloudflareAIProvider
from services.ai_providers.yandex import YandexGPTProvider

#: Реестр доступных провайдеров: имя (значение AI_PROVIDER) -> класс.
PROVIDER_REGISTRY: dict[str, type[AIProvider]] = {
    CloudflareAIProvider.name: CloudflareAIProvider,
    YandexGPTProvider.name: YandexGPTProvider,
}

#: Кэш синглтона для провайдера, выбранного конфигом.
_provider: AIProvider | None = None


def get_ai_provider(name: str | None = None) -> AIProvider:
    """
    Возвращает AI-провайдера.

    Без аргументов — синглтон, выбранный по AI_PROVIDER из .env
    (создаётся лениво при первом обращении и кэшируется).
    Явный аргумент name — для тестов/экспериментов: создаёт новый экземпляр
    без кэша (кроме случая, когда name совпадает с AI_PROVIDER).
    """
    global _provider
    if name is None:
        if _provider is not None:
            return _provider
        name = AI_PROVIDER

    provider_cls = PROVIDER_REGISTRY.get(name)
    if provider_cls is None:
        available = ", ".join(sorted(PROVIDER_REGISTRY))
        raise SystemExit(
            f"Неизвестный AI-провайдер: AI_PROVIDER='{name}'. "
            f"Доступны: {available}. Проверьте AI_PROVIDER в .env"
        )

    provider = provider_cls.from_config()
    if name == AI_PROVIDER:
        _provider = provider
    return provider
