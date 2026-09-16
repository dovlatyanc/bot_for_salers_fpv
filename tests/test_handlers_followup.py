"""«⏰ Напоминания»: базовый флоу + система напоминаний о follow-up (ЭТАП 2):
адресаты и рендер утренней сводки, рассылка, планировщик, last_daily."""

import asyncio
import contextlib
from datetime import date, datetime, timedelta

import pytest

import handlers.followup as fup
from handlers.followup import (
    DAILY_SEND_HOUR,
    _digest_due_now,
    _get_last_daily,
    _run_digest,
    _seconds_until,
    _set_last_daily,
    add_followup,
    button_followup,
    daily_recipients,
    get_active,
    mark_done,
    reminders_loop,
    render_digest,
    send_daily_digest,
)
from tests.fakes import FakeMsg, kb_data, make_state


@pytest.fixture(autouse=True)
def isolated_followups(tmp_path, monkeypatch):
    """data/followups.json — во временный файл (реальный не трогаем)."""
    path = tmp_path / "followups.json"
    monkeypatch.setattr(fup, "FOLLOWUPS_PATH", path)
    return path


class FakeBot:
    """Бот-фейк: send_message пишет в sends (или падает при fail=True)."""

    def __init__(self, fail: bool = False):
        self.sends = []
        self.fail = fail

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sends.append((chat_id, text, kwargs))


# ---------- базовый флоу ----------

async def test_create_and_done_flow():
    state = make_state()
    await button_followup(FakeMsg("⏰ Напоминания"), state)
    due = date.today() + timedelta(days=3)
    item = await add_followup(42, "Клиент @u, модель 6S4P", due)
    assert item["due"] == due.isoformat() and item["done"] is False
    assert await mark_done(42, item["id"])
    assert get_active(42) == []
    assert await mark_done(42, item["id"]) is False  # повторная отметка — no-op


# ---------- адресаты утренней сводки ----------

async def test_daily_recipients_filters_done_future_and_none():
    today = date.today()
    yesterday, tomorrow = today - timedelta(days=1), today + timedelta(days=1)
    await add_followup(11, "на сегодня", today)
    await add_followup(22, "просрочено", yesterday)
    await add_followup(33, "в будущем", tomorrow)
    await add_followup(44, "без срока", None)
    done = await add_followup(55, "выполнено", today)
    await mark_done(55, done["id"])
    assert daily_recipients() == [11, 22]
    # завтра: просроченные остаются, будущие подтягиваются
    assert daily_recipients(tomorrow) == [11, 22, 33]


# ---------- рендер сводки ----------

async def test_render_digest_none_without_due():
    await add_followup(42, "в будущем", date.today() + timedelta(days=5))
    await add_followup(42, "без срока", None)
    assert render_digest(42) is None
    assert render_digest(999) is None  # пользователь без напоминаний


# >>> ЧАСТЬ 2 ДОБАВЛЯЕТСЯ НИЖЕ <<<

async def test_render_digest_groups():
    yesterday = date.today() - timedelta(days=2)
    await add_followup(42, "просрочка", yesterday)
    await add_followup(42, "на сегодня", date.today())
    text = render_digest(42)
    assert "Follow-up" in text
    assert "🔴 Просроченные" in text and "🟡 На сегодня" in text
    assert "просрочка" in text and "на сегодня" in text
    assert yesterday.strftime("%d.%m") in text
    assert "⬅️ Открыть напоминания" in text


async def test_render_digest_cap_and_hidden():
    for i in range(fup.ACTIVE_LIMIT + 2):
        await add_followup(42, f"дело {i}", date.today())
    assert "и ещё 2" in render_digest(42)


# ---------- last_daily и время ----------

async def test_last_daily_roundtrip():
    assert _get_last_daily() is None
    day = date.today()
    await _set_last_daily(day)
    assert _get_last_daily() == day


def test_seconds_until():
    assert _seconds_until(9, datetime(2026, 9, 16, 8, 0, 0)) == 3600.0
    # 10:30 → до завтрашних 9:00 (22,5 ч)
    assert _seconds_until(9, datetime(2026, 9, 16, 10, 30, 0)) == 22.5 * 3600
    assert _seconds_until(9, datetime(2026, 9, 16, 9, 0, 0)) == 24 * 3600.0


async def test_digest_due_now():
    day = date.today()
    morning = datetime.now().replace(hour=DAILY_SEND_HOUR, minute=1)
    night = morning.replace(hour=3)
    assert _digest_due_now(day, morning) is True    # сводки ещё не было
    assert _digest_due_now(day, night) is False     # окно ещё не наступило
    await _set_last_daily(day)
    assert _digest_due_now(day, morning) is False   # уже отправлена


# ---------- рассылка ----------

async def test_send_daily_digest_delivers():
    await add_followup(11, "дело одиннадцать", date.today())
    await add_followup(22, "просрочка 22", date.today() - timedelta(days=1))
    bot = FakeBot()
    delivered = await send_daily_digest(bot)
    assert delivered == [11, 22]
    assert {uid for uid, _, _ in bot.sends} == {11, 22}
    for _, text, kwargs in bot.sends:
        assert kwargs["parse_mode"] == "HTML"
        assert "Follow-up" in text
    cbs = [cb for _, cb in kb_data(bot.sends[0][2]["reply_markup"])]
    assert "fup:list" in cbs and "fup:cancel" in cbs
    # рассылка не отмечает факт отправки — это делает _run_digest
    assert _get_last_daily() is None


async def test_send_daily_digest_raises_when_all_fail():
    await add_followup(11, "дело", date.today())
    with pytest.raises(RuntimeError):
        await send_daily_digest(FakeBot(fail=True))


async def test_send_daily_digest_no_recipients():
    bot = FakeBot()
    assert await send_daily_digest(bot) == []
    assert bot.sends == []


# ---------- _run_digest и планировщик ----------

async def test_run_digest_marks_last_daily():
    await add_followup(11, "дело", date.today())
    bot = FakeBot()
    await _run_digest(bot, date.today())
    assert _get_last_daily() == date.today()
    assert len(bot.sends) == 1


async def test_run_digest_retries_until_success(monkeypatch):
    monkeypatch.setattr(fup, "DIGEST_RETRY_DELAY", 0)
    await add_followup(11, "дело", date.today())

    class FlakyBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def send_message(self, chat_id, text, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("flaky")
            await super().send_message(chat_id, text, **kwargs)

    bot = FlakyBot()
    await _run_digest(bot, date.today())
    assert len(bot.sends) == 1
    assert _get_last_daily() == date.today()


async def test_reminders_loop_catchup_on_start(monkeypatch):
    """Догон при старте: сводка за сегодня не уходила → отправляется сразу."""
    monkeypatch.setattr(fup, "_seconds_until", lambda *a, **k: 0.05)
    monkeypatch.setattr(fup, "DAILY_SEND_HOUR", 0)   # окно наступило в любой час
    await add_followup(11, "дело из планировщика", date.today())
    bot = FakeBot()
    task = asyncio.create_task(reminders_loop(bot))
    await asyncio.sleep(0.3)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert any("дело из планировщика" in t for _, t, _ in bot.sends)
    assert _get_last_daily() == date.today()


async def test_reminders_loop_no_catchup_when_already_sent(monkeypatch):
    """Рестарт в тот же день после отправки — дубля нет (last_daily)."""
    monkeypatch.setattr(fup, "_seconds_until", lambda *a, **k: 0.05)
    monkeypatch.setattr(fup, "DAILY_SEND_HOUR", 0)
    await add_followup(11, "дело", date.today())
    await _set_last_daily(date.today())
    bot = FakeBot()
    task = asyncio.create_task(reminders_loop(bot))
    await asyncio.sleep(0.02)   # догона нет; плановый прогон (0.05 c) ещё не наступил
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert bot.sends == [], "рестарт в тот же день отправил дубль сводки"