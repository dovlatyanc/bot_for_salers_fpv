"""
Базовый контракт AI-провайдера.

Провайдер инкапсулирует всё общение с конкретным бэкендом (Cloudflare Worker,
YandexGPT и т.д.): HTTP-детали, форматы запросов/ответов, таймауты и
аутентификацию. Бизнес-логика бота (handlers/, ai/) работает только с
абстракцией AIProvider и переключается на другой бэкенд через переменную
AI_PROVIDER в .env, без правок кода обработчиков.

Контракт методов:
  ask(question) -> str
      Ответ ассистента на текстовый вопрос. Если провайдер не сконфигурирован,
      подсказка о настройке доходит до пользователя текстом: провайдер либо
      возвращает её сам (CloudflareAIProvider), либо бросает AIProviderError
      с текстом подсказки (YandexGPTProvider) — фасад ai.ask_ai в обоих
      случаях выдаёт пользователю сообщение. При HTTP/сетевых ошибках
      бросается AIProviderError.
  transcribe(audio) -> str
      Распознанный текст; пустая строка, если речь распознать не удалось.
      При любых ошибках бросается AIProviderError.

Добавление нового провайдера — см. docstring services/ai_providers/factory.py.
"""

from abc import ABC, abstractmethod


class AIProviderError(Exception):
    """
    Ошибка взаимодействия с AI-провайдером.

    message — человекочитаемый текст, безопасный для показа пользователю
              (str(exception) уже содержит готовую фразу);
    status  — HTTP-статус ответа, если ошибка пришла от бэкенда (иначе None);
    body    — тело ошибочного ответа, если доступно.
    """

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


class AIProvider(ABC):
    """Интерфейс AI-провайдера (запросы к ассистенту + распознавание речи)."""

    #: Имя провайдера — значение переменной AI_PROVIDER в .env (например, "cloudflare").
    name: str = "base"

    @classmethod
    @abstractmethod
    def from_config(cls) -> "AIProvider":
        """Создаёт провайдера из конфигурации приложения (config.py / .env).

        Каждый провайдер сам знает, какие переменные окружения ему нужны:
        фабрике достаточно вызвать from_config() у класса из реестра.
        """

    @abstractmethod
    async def ask(self, question: str) -> str:
        """Задаёт вопрос ассистенту и возвращает текст ответа (см. контракт выше)."""

    @abstractmethod
    async def transcribe(self, audio: bytes) -> str:
        """Распознаёт речь в аудиофайле и возвращает текст (см. контракт выше)."""
