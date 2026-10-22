"""
Отказоустойчивость AI-слоя: retry + circuit breaker + graceful degradation.

Активный провайдер (factory.get_ai_provider, выбор через AI_PROVIDER в .env)
оборачивается в ResilientAIProvider для рантайм-вызовов (ai/ai.py, handlers/):

  retry               — «переходящие» ошибки (сеть, таймаут, 429/5xx) повторяются
                        с экспоненциальным backoff. Ретраются только «быстрые»
                        сбои (уложились в FAST_FAIL_BUDGET): долгая попытка
                        (таймаут 90 c) не повторяется — пользователь уже ждал.
                        Провайдеры с собственным ретраем (YandexGPTProvider,
                        retries_internally = True) внешне не дублируются;
  circuit breaker     — CIRCUIT_FAILURE_THRESHOLD сбоев запросов подряд
                        (вызов ask/transcribe после исчерпания ретраев = один
                        сбой) размыкают цепь на CIRCUIT_COOLDOWN секунд:
                        ask() мгновенно возвращает случайную фразу-заглушку
                        (ai_fallback_phrase, prompts.py), не тратя таймауты,
                        transcribe() бросает AIProviderError(friendly=True)
                        с такой же фразой; после cooldown — пробный запрос
                        (half-open), успех снова замыкает цепь;
  graceful degradation — «не сконфигурирован» не считается сбоем: подсказка
                        провайдера доходит до пользователя как раньше; прочие
                        ошибки фасад (ai/ai.py) целиком пишет в системный лог,
                        пользователю показывает случайную фразу-заглушку из
                        пула AI_FALLBACK_PHRASES. Статические разделы
                        (Шпаргалки, FAQ, Сценарии, Возражения, Подбор) от AI
                        не зависят и работают при любом сбое.

Наблюдаемость: CircuitBreaker.health_report() и живая проба
ResilientAIProvider.health_check() — команда /health (только админ) в
handlers/fallback.py. Проба идёт мимо ретраев и брейкера (чистое наблюдение).
"""

import asyncio
import time

from config import system_logger
from services.ai_providers.base import AIProvider, AIProviderError
from services.ai_providers.factory import get_ai_provider
from services.ai_providers.prompts import AI_FALLBACK_PHRASES, ai_fallback_phrase

#: Retry: всего попыток (1 основная + RETRY_ATTEMPTS - 1 повторов), базовый
#: backoff (экспоненциально ×2) и его потолок.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 0.5
RETRY_BACKOFF_CAP = 2.0
#: Статусы, которые имеют смысл повторять (rate-limit + ошибки бэкенда).
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
#: Повторять только «быстрые» сбои: попытка, длившаяся дольше этого бюджета
#: (например, таймаут запроса), не ретраится — умножать ожидание незачем.
FAST_FAIL_BUDGET = 10.0

#: Circuit breaker: сбоев запросов подряд до размыкания и пауза (сек) до
#: пробного запроса (half-open).
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_COOLDOWN = 60.0

#: Текст деградации — не константа: при каждом срабатывании (разомкнутая цепь
#: в ask()/transcribe() или ошибка после исчерпания ретраев в фасаде ai/ai.py)
#: случайно выбирается дружелюбная фраза-заглушка из пула AI_FALLBACK_PHRASES
#: (prompts.py, аудит п. 2.3) — бот отвечает по-разному и выглядит «живым».

_STATE_LABELS = {
    "closed": "CLOSED (норма)",
    "open": "OPEN (разомкнут — мгновенные отказы)",
    "half-open": "HALF-OPEN (пробный запрос)",
}


def _is_transient(error: AIProviderError) -> bool:
    """Сетевые ошибки/таймауты (status is None) и 429/5xx — стоит повторить."""
    return error.status is None or error.status in RETRYABLE_STATUSES


class CircuitBreaker:
    """Счётчик сбоев подряд с размыканием цепи на cooldown (closed/open/half-open).

    closed — работаем; open — мгновенные отказы до конца cooldown; half-open —
    одна проба (allow_request() вернул True), успех замыкает цепь, неудача
    снова размыкает. Для тестов инжектируются clock и cooldown.
    """

    def __init__(
        self,
        failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        cooldown: float = CIRCUIT_COOLDOWN,
        clock=time.monotonic,
    ):
        self._threshold = failure_threshold
        self._cooldown = cooldown
        self._clock = clock
        self.state = "closed"
        self.consecutive_failures = 0
        self.total_failures = 0
        self.opened_at: float | None = None
        self.last_error: str | None = None

    def _elapsed_in_state(self) -> float:
        return self._clock() - self.opened_at if self.opened_at is not None else 0.0

    def allow_request(self) -> bool:
        """False — цепь разомкнута, запрос бессмыслен (мгновенный отказ)."""
        if self.state == "open" and self._elapsed_in_state() >= self._cooldown:
            self.state = "half-open"  # пробный запрос после паузы
        return self.state != "open"

    def record_success(self) -> None:
        self.state = "closed"
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self, error: str) -> None:
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_error = error[:300]
        if self.state == "half-open" or self.consecutive_failures >= self._threshold:
            self.state = "open"
            self.opened_at = self._clock()

    def health_report(self) -> str:
        """Человекочитаемый статус брейкера (без сетевых проб)."""
        lines = [
            f"Breaker: {_STATE_LABELS[self.state]}",
            f"Сбоев подряд: {self.consecutive_failures} (всего: {self.total_failures})",
        ]
        if self.state == "open":
            left = max(0.0, self._cooldown - self._elapsed_in_state())
            lines.append(f"Пробный запрос через: {left:.0f} с")
        if self.last_error:
            lines.append(f"Последняя ошибка: {self.last_error}")
        return "\n".join(lines)


# Общий брейкер рантайма: состояние переживает отдельные сообщения
# пользователей (синглтон провайдера тоже живёт в factory).
_BREAKER: CircuitBreaker | None = None


def get_breaker() -> CircuitBreaker:
    global _BREAKER
    if _BREAKER is None:
        _BREAKER = CircuitBreaker()
    return _BREAKER


def reset_breaker() -> None:
    """Сброс общего брейкера (для тестов/verify-скриптов)."""
    global _BREAKER
    _BREAKER = None


def get_resilient_provider() -> "ResilientAIProvider":
    """Активный провайдер из .env + общий брейкер — для рантайм-вызовов handlers/."""
    return ResilientAIProvider(get_ai_provider(), breaker=get_breaker())


class ResilientAIProvider(AIProvider):
    """Обёртка AIProvider: retry переходящих ошибок + circuit breaker.

    «Не сконфигурирован» — не сбой: подсказка провайдера проходит насквозь
    без брейкера и ретраев (ask() возвращает её текстом — как Cloudflare,
    transcribe() бросает AIProviderError — как Yandex), поведение вызывающего
    кода не меняется.
    """

    def __init__(self, inner: AIProvider, breaker: CircuitBreaker | None = None):
        self._inner = inner
        self.breaker = breaker if breaker is not None else CircuitBreaker()

    @classmethod
    def from_config(cls) -> "ResilientAIProvider":
        return cls(get_ai_provider(), breaker=get_breaker())

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def is_configured(self) -> bool:
        return self._inner.is_configured

    @property
    def inner(self) -> AIProvider:
        """Внутренний провайдер (для тестов и живой пробы в health_check)."""
        return self._inner

    def _attempts(self) -> int:
        """1 для провайдеров с собственным ретраем, иначе RETRY_ATTEMPTS."""
        return 1 if getattr(self._inner, "retries_internally", False) else RETRY_ATTEMPTS

    async def ask(self, question: str) -> str:
        if not self._inner.is_configured:
            try:
                return await self._inner.ask(question)
            except AIProviderError as e:
                return str(e)  # NOT_CONFIGURED_HINT — подсказка для пользователя
        if not self.breaker.allow_request():
            system_logger.warning(
                f"AI breaker OPEN ({self.name}): мгновенный отказ; "
                f"последняя ошибка: {self.breaker.last_error}"
            )
            return ai_fallback_phrase()
        return await self._with_retry(self._inner.ask, question)

    async def transcribe(self, audio: bytes) -> str:
        if not self._inner.is_configured:
            return await self._inner.transcribe(audio)  # подсказка-исключение
        if not self.breaker.allow_request():
            system_logger.warning(
                f"AI breaker OPEN ({self.name}): transcribe отклонён мгновенно"
            )
            raise AIProviderError(ai_fallback_phrase(), friendly=True)
        return await self._with_retry(self._inner.transcribe, audio)

    async def _with_retry(self, operation, *args):
        """Retry-цикл: переходящие «быстрые» сбои повторяются, исход — в брейкер."""
        attempts = self._attempts()
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                result = await operation(*args)
            except AIProviderError as e:
                fast = time.monotonic() - started <= FAST_FAIL_BUDGET
                if attempt < attempts and fast and _is_transient(e):
                    delay = min(RETRY_BACKOFF * 2 ** (attempt - 1), RETRY_BACKOFF_CAP)
                    system_logger.warning(
                        f"AI {self.name}: попытка {attempt}/{attempts} не удалась "
                        f"(status={e.status}), повтор через {delay:.1f} с"
                    )
                    await asyncio.sleep(delay)
                    continue
                self.breaker.record_failure(str(e))
                raise
            self.breaker.record_success()
            return result
        raise AssertionError("retry-цикл не может выйти без результата/ошибки")

    async def health_check(self) -> str:
        """Живой отчёт для /health (админ): конфигурация, брейкер, проба ask('ping').

        Проба идёт напрямую через внутренний провайдер — мимо ретраев и
        брейкера: наблюдение, не влияющее на состояние цепи.
        """
        lines = [
            f"Провайдер: {self.name} (сконфигурирован: {self.is_configured})",
            self.breaker.health_report(),
        ]
        if not self.is_configured:
            lines.append("Проба не выполнялась: провайдер не сконфигурирован")
            return "\n".join(lines)
        started = time.monotonic()
        try:
            answer = await self._inner.ask("ping")
            elapsed = time.monotonic() - started
            preview = (answer or "").strip().replace("\n", " ")[:80]
            lines.append(f"Проба ask('ping'): ✅ OK за {elapsed:.1f} с — «{preview}»")
        except AIProviderError as e:
            lines.append(f"Проба ask('ping'): ❌ {str(e)[:200]}")
        return "\n".join(lines)


