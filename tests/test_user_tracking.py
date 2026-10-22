"""Учёт пользователей и дневные счётчики (пп. 3.1): users, daily_counters,
UserTrackingMiddleware, CalcHistoryRepository.count_since, счётчик в ask_ai."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import db
from ai import ai as ai_facade
from db import counters, users
from db import calc_history
from db.engine import get_sessionmaker
from db.models import User
from middleware import UserTrackingMiddleware


async def _backdate(user_id: int, days: int) -> None:
    """Вручную состарить last_seen_at (репозиторий его только освежает)."""
    async with get_sessionmaker()() as session:
        row = await session.get(User, user_id)
        row.last_seen_at = datetime.now() - timedelta(days=days)
        await session.commit()


def _event(uid: int, username="u", first_name="Имя", is_bot=False):
    return SimpleNamespace(from_user=SimpleNamespace(
        id=uid, username=username, first_name=first_name, is_bot=is_bot))


# ---------- UserRepository.touch ----------

async def test_touch_creates_then_updates():
    assert await users.touch(101, "seller", "Иван") is True      # INSERT OR IGNORE
    assert await users.touch(101, "seller2", "Иван II") is False  # повтор — update
    assert await users.count_all() == 1
    async with get_sessionmaker()() as session:
        row = await session.get(User, 101)
        assert row.username == "seller2"           # ник освежается
        assert row.first_name == "Иван II"
        assert row.is_active is True


async def test_touch_none_names_keep_old_values():
    await users.touch(102, "nick", "Имя")
    await users.touch(102, None, None)             # нет данных — не затираем
    async with get_sessionmaker()() as session:
        row = await session.get(User, 102)
        assert row.username == "nick" and row.first_name == "Имя"


async def test_count_active_since_window():
    await users.touch(201)
    await users.touch(202)
    await _backdate(202, 8)                        # старее окна 7 дней
    assert await users.count_all() == 2
    assert await users.count_active_since(datetime.now() - timedelta(days=7)) == 1


# ---------- DailyCounterRepository ----------

async def test_counter_incr_accumulates_and_days_are_isolated():
    assert await counters.value("ai_ask") == 0     # нет строки — 0
    await counters.incr("ai_ask")
    await counters.incr("ai_ask", amount=4)
    await counters.incr("ai_voice", amount=2)
    assert await counters.value("ai_ask") == 5
    assert await counters.value("ai_voice") == 2
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    assert await counters.value("ai_ask", day=tomorrow) == 0


# ---------- CalcHistoryRepository.count_since ----------

async def test_calc_count_since_today():
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    baseline = await calc_history.count_since(midnight)
    await calc_history.add(42, {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "model": "6S2P", "cost": 10000, "price": 15000,
        "margin": 5000, "margin_pct": 33.3,
    })
    assert await calc_history.count_since(midnight) == baseline + 1
    assert await calc_history.count_since(datetime.now() + timedelta(days=1)) == 0


# ---------- UserTrackingMiddleware ----------

async def test_middleware_registers_user_and_passes_event_through():
    seen = []

    async def handler(event, data):
        seen.append(event)

    mw = UserTrackingMiddleware()
    ev = _event(301)
    await mw(handler, ev, {})
    assert seen == [ev]                            # событие дошло до хендлера
    assert await users.count_all() == 1
    await mw(handler, ev, {})                      # повтор — без дубля
    assert await users.count_all() == 1


async def test_middleware_skips_bots_and_events_without_user():
    async def handler(event, data):
        return "ok"

    mw = UserTrackingMiddleware()
    await mw(handler, SimpleNamespace(from_user=None), {})
    await mw(handler, _event(302, is_bot=True), {})
    assert await users.count_all() == 0


async def test_middleware_swallows_db_errors(monkeypatch):
    async def broken_touch(*args, **kwargs):
        raise RuntimeError("DB не инициализирована")

    monkeypatch.setattr(db.users, "touch", broken_touch)
    called = []

    async def handler(event, data):
        called.append(1)

    mw = UserTrackingMiddleware()
    await mw(handler, _event(303), {})             # сбой БД не роняет обработку
    assert called == [1]


# ---------- Счётчик обращений к AI в фасаде ----------

class _FakeOk:
    name = "fake"
    is_configured = True

    async def ask(self, question: str) -> str:
        return "ответ"


async def test_ask_ai_increments_counter(monkeypatch):
    before = await counters.value("ai_ask")
    monkeypatch.setattr(ai_facade, "get_ai_provider", lambda: _FakeOk())
    assert await ai_facade.ask_ai("тест") == "ответ"
    assert await counters.value("ai_ask") == before + 1
