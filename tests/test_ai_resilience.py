"""services/ai_providers/resilience.py: retry, circuit breaker, деградация, health."""

import asyncio

import pytest

import services.ai_providers.resilience as rs
from services.ai_providers import AIProvider, AIProviderError
from services.ai_providers.prompts import AI_FALLBACK_PHRASES, ai_fallback_phrase


class _Flaky(AIProvider):
    """Фейковый провайдер: первые failures вызовов бросают AIProviderError."""

    name = "flaky"

    def __init__(self, failures=0, status=None, delay=0.0):
        self.failures = failures
        self.status = status
        self.delay = delay
        self.calls = 0

    @classmethod
    def from_config(cls):
        return cls()

    @property
    def is_configured(self):
        return True

    async def _maybe_fail(self):
        self.calls += 1
        if self.calls <= self.failures:
            if self.delay:
                await asyncio.sleep(self.delay)
            raise AIProviderError(f"boom #{self.calls}", status=self.status)
        return True

    async def ask(self, question):
        await self._maybe_fail()
        return f"ok:{question}"

    async def transcribe(self, audio):
        await self._maybe_fail()
        return "text"


class _NotConfigured(_Flaky):
    """Мимикрия под yandex без ключей: подсказка уходит исключением."""

    name = "notconf"

    @property
    def is_configured(self):
        return False

    async def ask(self, question):
        self.calls += 1
        raise AIProviderError("подсказка о настройке")

    async def transcribe(self, audio):
        self.calls += 1
        raise AIProviderError("подсказка о настройке")


class _SelfRetrying(_Flaky):
    """Мимикрия под yandex: ретраит переходящие ошибки сам."""

    retries_internally = True


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    """Backoff = 0 (без пауз) и чистый общий брейкер на каждый тест."""
    monkeypatch.setattr(rs, "RETRY_BACKOFF", 0)
    monkeypatch.setattr(rs, "RETRY_BACKOFF_CAP", 0)
    rs.reset_breaker()
    yield
    rs.reset_breaker()


# ---------- circuit breaker ----------

def test_breaker_states_and_recovery():
    now = {"t": 0.0}
    br = rs.CircuitBreaker(clock=lambda: now["t"])
    assert br.state == "closed" and br.allow_request()

    for _ in range(rs.CIRCUIT_FAILURE_THRESHOLD):
        br.record_failure("e1")
    assert br.state == "open"
    assert not br.allow_request()  # мгновенный отказ
    assert "e1" in br.health_report() and "OPEN" in br.health_report()

    now["t"] = rs.CIRCUIT_COOLDOWN + 1
    assert br.allow_request() and br.state == "half-open"  # одна проба
    br.record_failure("e2")  # проба не удалась — снова разомкнуто
    assert br.state == "open"

    now["t"] = 2 * rs.CIRCUIT_COOLDOWN + 5
    assert br.allow_request()
    br.record_success()  # проба удалась — цепь замкнулась
    assert br.state == "closed"
    assert br.consecutive_failures == 0
    assert br.total_failures == rs.CIRCUIT_FAILURE_THRESHOLD + 1


def test_breaker_threshold_not_reached():
    br = rs.CircuitBreaker()
    br.record_failure("e")
    br.record_failure("e")
    assert br.state == "closed" and br.allow_request()


# ---------- retry ----------

async def test_retry_transient_then_success():
    flaky = _Flaky(failures=2, status=503)
    p = rs.ResilientAIProvider(flaky)
    assert await p.ask("q") == "ok:q"
    assert flaky.calls == 3  # 1 основная + 2 повтора


async def test_non_transient_status_fails_fast():
    flaky = _Flaky(failures=9, status=400)  # 4xx не переходящая — без ретраев
    p = rs.ResilientAIProvider(flaky)
    with pytest.raises(AIProviderError, match="boom #1"):
        await p.ask("q")
    assert flaky.calls == 1


async def test_slow_failure_is_not_retried(monkeypatch):
    # Попытка дольше FAST_FAIL_BUDGET (таймаут запроса) — повтор бессмыслен
    monkeypatch.setattr(rs, "FAST_FAIL_BUDGET", 0)
    flaky = _Flaky(failures=1, status=500, delay=0.01)
    p = rs.ResilientAIProvider(flaky)
    with pytest.raises(AIProviderError):
        await p.ask("q")
    assert flaky.calls == 1


async def test_self_retrying_provider_not_doubled():
    # Провайдер с retries_internally=True (yandex) ретраит сам — снаружи 1 попытка
    flaky = _SelfRetrying(failures=9, status=429)
    p = rs.ResilientAIProvider(flaky)
    with pytest.raises(AIProviderError):
        await p.ask("q")
    assert flaky.calls == 1


async def test_retry_exhausted_opens_circuit():
    flaky = _Flaky(failures=99, status=500)
    p = rs.ResilientAIProvider(flaky)
    for _ in range(rs.CIRCUIT_FAILURE_THRESHOLD):  # 3 запроса → цепь разомкнута
        with pytest.raises(AIProviderError):
            await p.ask("q")
    assert p.breaker.state == "open"
    calls = flaky.calls
    # Разомкнутая цепь: мгновенная фраза-заглушка, к провайдеру не ходим
    assert await p.ask("q") in rs.AI_FALLBACK_PHRASES
    assert flaky.calls == calls


# ---------- graceful degradation ----------

async def test_not_configured_ask_passthrough_no_breaker():
    inner = _NotConfigured(failures=99)
    p = rs.ResilientAIProvider(inner)
    assert await p.ask("q") == "подсказка о настройке"
    assert inner.calls == 1
    assert p.breaker.state == "closed"  # «не сконфигурирован» — не сбой


async def test_transcribe_not_configured_passthrough():
    inner = _NotConfigured(failures=0)
    p = rs.ResilientAIProvider(inner)
    with pytest.raises(AIProviderError, match="подсказка о настройке"):
        await p.transcribe(b"a")
    assert inner.calls == 1 and p.breaker.state == "closed"


async def test_transcribe_retry_then_success():
    flaky = _Flaky(failures=2, status=None)  # сетевая ошибка — переходящая
    p = rs.ResilientAIProvider(flaky)
    assert await p.transcribe(b"a") == "text"
    assert flaky.calls == 3


async def test_transcribe_open_circuit_raises_hint():
    flaky = _Flaky(failures=99, status=502)
    p = rs.ResilientAIProvider(flaky)
    for _ in range(rs.CIRCUIT_FAILURE_THRESHOLD):
        with pytest.raises(AIProviderError):
            await p.transcribe(b"a")
    assert p.breaker.state == "open"
    calls = flaky.calls
    with pytest.raises(AIProviderError) as ei:
        await p.transcribe(b"a")
    # Ошибка — дружелюбная заглушка (random из пула), не техническая деталь
    assert ei.value.friendly
    assert str(ei.value) in rs.AI_FALLBACK_PHRASES
    assert flaky.calls == calls


# ---------- health ----------

async def test_health_check_ok():
    p = rs.ResilientAIProvider(_Flaky(failures=0))
    report = await p.health_check()
    assert "сконфигурирован: True" in report
    assert "Breaker: CLOSED" in report and "✅" in report


async def test_health_check_probe_fail_does_not_touch_breaker():
    p = rs.ResilientAIProvider(_Flaky(failures=99, status=500))
    report = await p.health_check()
    assert "❌" in report
    assert p.breaker.state == "closed"  # проба — чистое наблюдение


async def test_health_check_unconfigured():
    p = rs.ResilientAIProvider(_NotConfigured(failures=0))
    report = await p.health_check()
    assert "сконфигурирован: False" in report
    assert "не сконфигурирован" in report


# ---------- пул фраз-заглушек (аудит п. 2.3) ----------

def test_fallback_pool_integrity():
    assert len(AI_FALLBACK_PHRASES) >= 5                       # пул, а не пара фраз
    assert len(set(AI_FALLBACK_PHRASES)) == len(AI_FALLBACK_PHRASES)  # без дублей
    for phrase in AI_FALLBACK_PHRASES:
        assert phrase and phrase == phrase.strip()             # непустые, без рамок
        assert not ({"<", ">", "&"} & set(phrase))             # HTML-безопасны
    assert ai_fallback_phrase() in AI_FALLBACK_PHRASES         # выбор только из пула


def test_fallback_phrase_random_pick(monkeypatch):
    """ai_fallback_phrase() выбирает случайный элемент всего пула."""
    import services.ai_providers.prompts as prompts

    seen = []
    monkeypatch.setattr(
        prompts.random, "choice",
        lambda seq: (seen.append(list(seq)), seq[1])[1],
    )
    assert ai_fallback_phrase() == AI_FALLBACK_PHRASES[1]
    assert seen == [list(AI_FALLBACK_PHRASES)]                 # выбор по всему пулу

