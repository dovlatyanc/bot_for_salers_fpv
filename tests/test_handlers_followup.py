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
    parse_due_at,
    reminders_loop,
    render_digest,
    send_daily_digest,
    send_upcoming_reminders,
    upcoming_loop,
)
from tests.fakes import FakeMsg, kb_data, make_state


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
    assert await get_active(42) == []
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
    assert await daily_recipients() == [11, 22]
    # завтра: просроченные остаются, будущие подтягиваются
    assert await daily_recipients(tomorrow) == [11, 22, 33]


# ---------- рендер сводки ----------

async def test_render_digest_none_without_due():
    await add_followup(42, "в будущем", date.today() + timedelta(days=5))
    await add_followup(42, "без срока", None)
    assert await render_digest(42) is None
    assert await render_digest(999) is None  # пользователь без напоминаний


# >>> ЧАСТЬ 2 ДОБАВЛЯЕТСЯ НИЖЕ <<<

async def test_render_digest_groups():
    yesterday = date.today() - timedelta(days=2)
    await add_followup(42, "просрочка", yesterday)
    await add_followup(42, "на сегодня", date.today())
    text = await render_digest(42)
    assert "Follow-up" in text
    assert "🔴 Просроченные" in text and "🟡 На сегодня" in text
    assert "просрочка" in text and "на сегодня" in text
    assert yesterday.strftime("%d.%m") in text
    assert "⬅️ Открыть напоминания" in text


async def test_render_digest_cap_and_hidden():
    for i in range(fup.ACTIVE_LIMIT + 2):
        await add_followup(42, f"дело {i}", date.today())
    assert "и ещё 2" in await render_digest(42)


# ---------- last_daily и время ----------

async def test_last_daily_roundtrip():
    assert await _get_last_daily() is None
    day = date.today()
    await _set_last_daily(day)
    assert await _get_last_daily() == day


def test_seconds_until():
    assert _seconds_until(9, datetime(2026, 9, 16, 8, 0, 0)) == 3600.0
    # 10:30 → до завтрашних 9:00 (22,5 ч)
    assert _seconds_until(9, datetime(2026, 9, 16, 10, 30, 0)) == 22.5 * 3600
    assert _seconds_until(9, datetime(2026, 9, 16, 9, 0, 0)) == 24 * 3600.0


async def test_digest_due_now():
    day = date.today()
    morning = datetime.now().replace(hour=DAILY_SEND_HOUR, minute=1)
    night = morning.replace(hour=3)
    assert await _digest_due_now(day, morning) is True    # сводки ещё не было
    assert await _digest_due_now(day, night) is False     # окно ещё не наступило
    await _set_last_daily(day)
    assert await _digest_due_now(day, morning) is False   # уже отправлена


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
    assert await _get_last_daily() is None


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
    assert await _get_last_daily() == date.today()
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
    assert await _get_last_daily() == date.today()


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
    assert await _get_last_daily() == date.today()


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


# ---------- ЭТАП 3: предварительные напоминания «за 10/5/2 минуты» ----------

def test_parse_due_at_variants():
    now = datetime(2026, 9, 16, 10, 0)
    assert parse_due_at("через 30 минут", now) == now + timedelta(minutes=30)
    assert parse_due_at("через 2 часа", now) == now + timedelta(hours=2)
    assert parse_due_at("через 90 секунд", now) == now + timedelta(seconds=90)
    # дата без времени — точный момент неизвестен
    assert parse_due_at("через 3 дня", now) is None
    assert parse_due_at("завтра", now) is None
    assert parse_due_at("25.09", now) is None
    # дата + время / время без даты
    assert parse_due_at("через 3 дня в 15:00", now) == datetime(2026, 9, 19, 15, 0)
    assert parse_due_at("завтра в 15:40", now) == datetime(2026, 9, 17, 15, 40)
    assert parse_due_at("25.09 в 9:05", now) == datetime(2026, 9, 25, 9, 5)
    assert parse_due_at("в 15:40", now) == datetime(2026, 9, 16, 15, 40)
    assert parse_due_at("в 08:00", now) == datetime(2026, 9, 17, 8, 0)   # прошло → завтра
    assert parse_due_at("позвонить клиенту", now) is None
    assert parse_due_at("позвонить в 25:00", now) is None                # бредовое время


async def test_add_followup_stores_due_at():
    moment = (datetime.now() + timedelta(minutes=30)).replace(microsecond=0)
    item = await add_followup(42, "созвон", moment.date(), moment)
    assert item["due_at"] == moment.isoformat()
    assert item["due"] == moment.date().isoformat()
    plain = await add_followup(42, "без точного времени", date.today())
    assert plain["due_at"] is None


async def test_render_list_shows_exact_time():
    moment = (datetime.now() + timedelta(hours=2)).replace(microsecond=0)
    await add_followup(42, "точно в срок", moment.date(), moment)
    text = fup._render_list(await get_active(42))
    assert moment.strftime("%H:%M") in text


async def test_process_text_saves_exact_moment_and_hint():
    state = make_state()
    await button_followup(FakeMsg("⏰ Напоминания"), state)
    msg = FakeMsg("созвон с клиентом через 45 минут")
    await fup.process_followup_text(msg, state)
    items = await get_active(42)
    assert len(items) == 1 and items[0]["due_at"] is not None
    assert "Пришлю напоминание за 10, 5 и 2 минуты" in msg.answers[-1][0]


async def test_send_upcoming_offsets_and_dedup():
    t0 = datetime.now().replace(microsecond=0)
    due9 = t0 + timedelta(minutes=9)
    await add_followup(11, "созвон с клиентом", due9.date(), due9)
    bot = FakeBot()
    # 9 минут до события: активен интервал «за 10» → одно сообщение
    assert await send_upcoming_reminders(bot, now=t0) == 1
    uid, text, kwargs = bot.sends[0]
    assert uid == 11 and kwargs["parse_mode"] == "HTML"
    assert "Через 9 минут" in text and "созвон с клиентом" in text
    assert "📅" in text                              # дата/время события показаны
    cbs = [cb for _, cb in kb_data(kwargs["reply_markup"])]
    assert "fup:list" in cbs and "fup:cancel" in cbs
    # повторный скан в тот же момент — дубля нет (журнал followup_reminders)
    assert await send_upcoming_reminders(bot, now=t0) == 0
    assert len(bot.sends) == 1
    # -5 минут
    assert await send_upcoming_reminders(bot, now=t0 + timedelta(minutes=4)) == 1
    assert "Через 5 минут" in bot.sends[-1][1]
    # -2 минуты
    assert await send_upcoming_reminders(bot, now=t0 + timedelta(minutes=7)) == 1
    assert "Через 2 минуты" in bot.sends[-1][1]
    # событие прошло — тишина
    assert await send_upcoming_reminders(bot, now=t0 + timedelta(minutes=20)) == 0
    assert len(bot.sends) == 3


async def test_send_upcoming_skips_past_done_and_far():
    t0 = datetime.now().replace(microsecond=0)
    await add_followup(11, "уже наступило", t0.date(), t0 - timedelta(minutes=1))
    await add_followup(12, "далёкое", (t0 + timedelta(minutes=30)).date(),
                       t0 + timedelta(minutes=30))
    bot = FakeBot()
    assert await send_upcoming_reminders(bot, now=t0) == 0
    assert bot.sends == []
    live = await add_followup(11, "сделано заранее", (t0 + timedelta(minutes=3)).date(),
                              t0 + timedelta(minutes=3))
    await mark_done(11, live["id"])                # выполненное не напоминаем
    assert await send_upcoming_reminders(bot, now=t0) == 0
    assert bot.sends == []


async def test_send_upcoming_retries_after_failure():
    """Неудавшаяся отправка не фиксируется в журнале → ретрай на следующем скане."""
    t0 = datetime.now().replace(microsecond=0)
    due1 = t0 + timedelta(minutes=1)
    await add_followup(11, "ретрай", due1.date(), due1)
    assert await send_upcoming_reminders(FakeBot(fail=True), now=t0) == 0
    bot = FakeBot()
    # событие через минуту: все три интервала уходят разом, от большего
    assert await send_upcoming_reminders(bot, now=t0) == 3
    assert all("Через 1 минуту" in t for _, t, _ in bot.sends)
    assert await send_upcoming_reminders(bot, now=t0) == 0


async def test_upcoming_loop_sends_periodically(monkeypatch):
    monkeypatch.setattr(fup, "PRE_SCAN_SECONDS", 0.05)
    t0 = datetime.now().replace(microsecond=0)
    due9 = t0 + timedelta(minutes=9)
    await add_followup(11, "живой сканер", due9.date(), due9)
    bot = FakeBot()
    task = asyncio.create_task(upcoming_loop(bot))
    await asyncio.sleep(0.3)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    # событие через 9 минут: активен только интервал «за 10» → ровно одно сообщение,
    # повторные сканы каждые 0.05 c дублей не добавляют
    assert any("Через 9 минут" in t and "живой сканер" in t for _, t, _ in bot.sends)
    assert len(bot.sends) == 1, "повторные сканы дали дубли"