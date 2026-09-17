"""
Модуль «⏰ Напоминания» — личные дела менеджера по клиентам.

Сценарий:
  «⏰ Напоминания» (reply) — список активных напоминаний группами:
                              🔴 просроченные / 🟡 на сегодня / 🔵 предстоящие /
                              ⚪ без срока, под каждым кнопка «✅ Выполнить»;
  ➕ Новое напоминание      — FSM FollowUpState.waiting_for_text: менеджер шлёт
                              один текст, например:
                              «Клиент @username, модель 6S4P, позвонить через 3 дня»;
                              бот вытаскивает срок («через 3 дня», «через 2 недели»,
                              «завтра», «послезавтра», «сегодня», «25.09»,
                              «25.09.2026», «в 15:40», «через 2 часа») и сохраняет;
                              без срока — в группу «Без срока»;
  fup:done:<id>            — отметить выполненным;
  fup:done_list            — последние выполненные;
  fup:undone:<id>          — вернуть в активные;
  fup:del:<id>             — удалить выполненное;
  fup:list / fup:cancel    — к списку / закрыть.

Хранение: SQLite через пакет db — таблица follow_ups; ключ last_daily — в
таблице settings. Первичный перенос из data/followups.json выполняет db.seed
при старте (одноразово; JSON остаётся как резервная копия).

Система напоминаний о follow-up (ЭТАП 2): reminders_loop(bot) запускается из
bot.py фоновым asyncio-заданием и ежедневно в DAILY_SEND_HOUR:00 рассылает
утреннюю сводку (🔴 просроченные / 🟡 на сегодня) всем, у кого такие
напоминания есть: daily_recipients → render_digest → send_daily_digest.
Факт отправки за сутки — ключ last_daily в таблице settings: рестарт в
течение дня дубль не отправит, а пропущенная сводка догоняется при старте;
неудавшаяся рассылка повторяется каждые DIGEST_RETRY_DELAY секунд до конца
суток.

ЭТАП 3 — предварительные напоминания: upcoming_loop(bot) (второе фоновое
задание в bot.py) каждые PRE_SCAN_SECONDS находит активные напоминания с
точным временем (due_at: «в 15:40», «через 2 часа», «завтра в 15:40») и за
каждое из PRE_REMIND_MINUTES (10, 5 и 2 минуты) до события рассылает
«⏰ Через N минут…». Отправки фиксируются в таблице followup_reminders
(уникальная пара follow_up_id+minutes) — рестарт и повторные сканы дублей
не дают; неудавшаяся отправка повторится на следующем скане.
"""

import asyncio
import html
import json
import logging
import math
import re
from datetime import date, datetime, time, timedelta

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db import followups as followups_db, settings as settings_db
from keyboards import MENU_EXIT_BUTTONS

router = Router(name="followup")

dialog_logger = logging.getLogger("dialog")
system_logger = logging.getLogger("system")

FOLLOWUP_BUTTON = "⏰ Напоминания"

DAILY_SEND_HOUR = 9       # час утренней сводки о follow-up (reminders_loop)
DIGEST_RETRY_DELAY = 300  # повтор неудавшейся рассылки сводки, сек
ACTIVE_LIMIT = 20         # максимум напоминаний на экране (список и сводка)
DONE_LIMIT = 10           # сколько выполненных показывать
PRE_REMIND_MINUTES = (10, 5, 2)  # «за сколько минут» предупреждать (upcoming_loop)
PRE_SCAN_SECONDS = 20            # период сканирования upcoming_loop, сек

# Кнопки меню (+ легаси «📊 ТТХ»), по которым можно выйти из ввода напоминания
# (сама «⏰ Напоминания» — перезапуск экрана, а не выход)
MENU_BUTTONS = frozenset(MENU_EXIT_BUTTONS) - {FOLLOWUP_BUTTON}
EXIT_WORDS = frozenset({"отмена", "cancel", "❌", "❌ отмена"})


class FollowUpState(StatesGroup):
    waiting_for_text = State()   # шаг: менеджер диктует текст напоминания


# ============================================================
# ХРАНИЛИЩЕ: SQLite через пакет db (таблица follow_ups + settings)
# ============================================================
async def add_followup(user_id: int, text: str, due: date | None,
                       due_at: datetime | None = None) -> dict:
    """Сохраняет напоминание и возвращает его dict (id — сквозной номер).

    due_at — точный момент события, если время известно из текста;
    по нему upcoming_loop шлёт напоминания «за 10/5/2 минуты».
    """
    return await followups_db.add(user_id, text, due, due_at)


async def mark_done(user_id: int, item_id: int) -> bool:
    """Отмечает выполненным; False — не найдено или уже отмечено."""
    return await followups_db.mark_done(user_id, item_id)


async def reopen(user_id: int, item_id: int) -> bool:
    """Возвращает в активные; False — не найдено или уже активно."""
    return await followups_db.reopen(user_id, item_id)


async def delete_followup(user_id: int, item_id: int) -> bool:
    """Удаляет напоминание менеджера; False — чужое или не найдено."""
    return await followups_db.delete(user_id, item_id)


async def get_active(user_id: int) -> list[dict]:
    """Активные (не выполненные) напоминания менеджера — по возрастанию срока."""
    return await followups_db.get_active(user_id)


async def get_done(user_id: int, limit: int = DONE_LIMIT) -> list[dict]:
    """Выполненные — свежие отметки первыми."""
    return await followups_db.get_done(user_id, limit)


async def collect_due(user_id: int, on_date: date | None = None) -> list[dict]:
    """Активные напоминания со сроком ровно на дату (хук утренней сводки)."""
    return await followups_db.collect_due(user_id, on_date)


# ============================================================
# РАЗБОР СРОКА ИЗ ТЕКСТА
# ============================================================
_RE_IN = re.compile(r"через\s+(\d+)\s*([а-яa-z]+)", re.IGNORECASE)
_RE_DATE = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b")


def parse_due(text: str, today: date | None = None) -> date | None:
    """Вытаскивает срок из текста напоминания (None — срока нет).

    Понимает: «через 3 дня/дн», «через 2 недели», «через 30 минут» (сегодня),
    «завтра», «послезавтра», «сегодня», «25.09», «25.09.2026».
    """
    day = today or date.today()
    t = text.lower()
    if re.search(r"\bпослезавтра\b", t):
        return day + timedelta(days=2)
    if re.search(r"\bзавтра\b", t):
        return day + timedelta(days=1)
    if re.search(r"\bсегодня\b", t):
        return day
    m = _RE_IN.search(t)
    if m:
        n, word = int(m.group(1)), m.group(2)
        if word.startswith("н"):                       # неделю/недели/недель
            return day + timedelta(days=7 * n)
        if word.startswith(("ч", "м", "сек")):         # часы/минуты/секунды → сегодня
            return day
        return day + timedelta(days=n)                 # день/дня/дней/дн
    for m in _RE_DATE.finditer(t):
        d_, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (1 <= d_ <= 31 and 1 <= mo <= 12):
            continue
        if y:
            year = int(y)
            year += 2000 if year < 100 else 0
        else:
            year = day.year
        try:
            parsed = date(year, mo, d_)
        except ValueError:
            continue
        if not y and parsed < day:
            parsed = date(year + 1, mo, d_)            # «25.09» из прошлого → следующий год
        return parsed
    return None


_RE_TIME = re.compile(r"\b(?:в|во)?\s*(\d{1,2}):(\d{2})\b")


def parse_due_at(text: str, now: datetime | None = None) -> datetime | None:
    """Точный момент события из текста (None — момент не известен до минуты).

    Возвращает datetime только когда время известно:
      «через 30 минут», «через 2 часа», «через 90 секунд», «завтра в 15:40»,
      «25.09 в 9:00», «в 15:40» (сегодня; если время уже прошло — завтра).
    Дата без времени («через 3 дня», «завтра», «25.09») → None: напоминать
    «за N минут» до полуночи бессмысленно — её сопровождает только сводка.
    """
    now = now or datetime.now()
    t = text.lower()
    m = _RE_IN.search(t)
    if m and m.group(2).startswith(("ч", "м", "сек")):   # часы/минуты/секунды
        n, word = int(m.group(1)), m.group(2)
        if word.startswith("ч"):
            return now + timedelta(hours=n)
        if word.startswith("м"):
            return now + timedelta(minutes=n)
        return now + timedelta(seconds=n)
    tm = _RE_TIME.search(t)
    if tm is None:
        return None
    try:
        moment = time(int(tm.group(1)), int(tm.group(2)))
    except ValueError:
        return None
    day = parse_due(text, now.date())
    if day is None:          # «в 15:40» без даты: сегодня, если ещё не прошло
        day = (now.date() if datetime.combine(now.date(), moment) > now
               else now.date() + timedelta(days=1))
    return datetime.combine(day, moment)


def _parse_iso_date(iso) -> date | None:
    try:
        return date.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None


def _fmt_due(d: date, today: date) -> str:
    text = f"{d.day:02d}.{d.month:02d}"
    if d.year != today.year:
        text += f".{d.year}"
    return text


def _parse_dt_local(value) -> datetime | None:
    """ISO-строка → datetime (None — битое/пустое значение)."""
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _fmt_moment(moment: datetime, today: date) -> str:
    """«19.09 15:40» (год — только если не текущий)."""
    text = f"{moment.day:02d}.{moment.month:02d}"
    if moment.year != today.year:
        text += f".{moment.year}"
    return f"{text} {moment:%H:%M}"


def _minutes_word(n: int) -> str:
    """«минуту/минуты/минут»: через 1 минуту, через 3 минуты, через 5 минут."""
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return "минуту"
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return "минуты"
    return "минут"


def _fmt_stamp(iso) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d.%m %H:%M")
    except (TypeError, ValueError):
        return "—"


def _render_list(items: list[dict], today: date | None = None) -> str:
    """Список активных напоминаний группами: просроченные/сегодня/предстоящие/без срока."""
    today = today or date.today()
    if not items:
        return ("⏰ <b>Напоминания</b>\n\nПока пусто. Нажмите «➕ Новое напоминание».\n\n"
                "Пример:\n<i>Клиент @username, модель 6S4P, позвонить через 3 дня</i>")
    groups: dict[str, list[tuple[date | None, dict]]] = {
        "over": [], "today": [], "future": [], "none": [],
    }
    for it in items:
        d = _parse_iso_date(it.get("due"))
        key = "none" if d is None else ("over" if d < today else "today" if d == today else "future")
        groups[key].append((d, it))
    titles = [("over", "🔴 Просроченные"), ("today", "🟡 На сегодня"),
              ("future", "🔵 Предстоящие"), ("none", "⚪ Без срока")]
    lines = ["⏰ <b>Напоминания</b>\n"]
    shown = 0
    for key, title in titles:
        pairs = sorted(groups[key], key=lambda p: (p[0] or date.min, p[1].get("id", 0)))
        if not pairs:
            continue
        lines.append(f"\n<b>{title}:</b>")
        for d, it in pairs:
            if shown >= ACTIVE_LIMIT:
                break
            due_at = _parse_dt_local(it.get("due_at"))
            if due_at:
                when = f"{_fmt_moment(due_at, today)} — "
            elif d:
                when = f"{_fmt_due(d, today)} — "
            else:
                when = ""
            lines.append(f"• {when}{html.escape(str(it.get('text', '')))}")
            shown += 1
    hidden = len(items) - shown
    if hidden > 0:
        lines.append(f"\n…и ещё {hidden} — отметьте или удалите старые.")
    lines.append("\n✅ — отметить выполненным; добавить — «➕ Новое напоминание».")
    return "\n".join(lines)


def _render_done(items: list[dict]) -> str:
    if not items:
        return "🗂 <b>Выполненные</b>\n\nПока пусто."
    lines = ["🗂 <b>Выполненные</b> — последние отметки:\n"]
    for it in items:
        lines.append(f"• {_fmt_stamp(it.get('done_at'))} — {html.escape(str(it.get('text', '')))}")
    lines.append("\n↩️ — вернуть в активные, 🗑 — удалить совсем.")
    return "\n".join(lines)


# ============================================================
# ЭТАП 2: СИСТЕМА НАПОМИНАНИЙ О FOLLOW-UP (утренняя сводка)
# ============================================================
# reminders_loop(bot) запускается из bot.py фоновым asyncio-заданием:
# ежедневно в DAILY_SEND_HOUR:00 каждому, у кого есть просроченные или
# «на сегодня» напоминания, уходит сводка. Факт отправки за сутки — ключ
# last_daily в таблице settings (защита от дубля при рестарте; при
# старте пропущенная за сегодня сводка догоняется).


async def _get_last_daily() -> date | None:
    """Дата последней успешной утренней сводки (ключ last_daily в settings)."""
    return _parse_iso_date(await settings_db.get("last_daily"))


async def _set_last_daily(day: date) -> None:
    await settings_db.set("last_daily", day.isoformat())


async def daily_recipients(on_date: date | None = None) -> list[int]:
    """user_id всех менеджеров с активными напоминаниями на дату или ранее.

    Выполненные, будущие и «без срока» в рассылку не попадают.
    """
    return await followups_db.daily_recipients(on_date)


async def render_digest(user_id: int, on_date: date | None = None) -> str | None:
    """Текст утренней сводки: 🔴 просроченные + 🟡 на сегодня.

    None — напоминаний к отправке нет (менеджера не беспокоим).
    """
    day = on_date or date.today()
    today_items = await collect_due(user_id, day)
    over_items: list[tuple[date, dict]] = []
    for it in await get_active(user_id):
        d = _parse_iso_date(it.get("due"))
        if d is not None and d < day:
            over_items.append((d, it))
    if not today_items and not over_items:
        return None
    lines = ["⏰ <b>Follow-up на сегодня</b>\n"]
    shown = 0
    if over_items:
        lines.append("\n<b>🔴 Просроченные:</b>")
        for d, it in sorted(over_items, key=lambda p: (p[0], p[1].get("id", 0))):
            if shown >= ACTIVE_LIMIT:
                break
            lines.append(f"• {_fmt_due(d, day)} — {html.escape(str(it.get('text', '')))}")
            shown += 1
    if today_items:
        lines.append("\n<b>🟡 На сегодня:</b>")
        for it in today_items:
            if shown >= ACTIVE_LIMIT:
                break
            lines.append(f"• {html.escape(str(it.get('text', '')))}")
            shown += 1
    hidden = len(today_items) + len(over_items) - shown
    if hidden > 0:
        lines.append(f"\n…и ещё {hidden} — откройте «Напоминания».")
    lines.append("\nОтметить выполненным — «⬅️ Открыть напоминания».")
    return "\n".join(lines)


def _digest_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ Открыть напоминания", callback_data="fup:list")],
        [types.InlineKeyboardButton(text="❌ Закрыть", callback_data="fup:cancel")],
    ])


async def send_daily_digest(bot, on_date: date | None = None) -> list[int]:
    """Рассылает утреннюю сводку всем адресатам daily_recipients.

    Возвращает user_id доставившихся; RuntimeError — адресаты были,
    но не доставлено никому (планировщик повторит попытку). Факт отправки
    (last_daily) отмечает вызывающий _run_digest, а не сама рассылка.
    """
    day = on_date or date.today()
    pairs: list[tuple[int, str]] = []
    for uid in await daily_recipients(day):
        text = await render_digest(uid, day)
        if text:
            pairs.append((uid, text))
    if not pairs:
        return []
    delivered: list[int] = []
    for uid, text in pairs:
        try:
            await bot.send_message(uid, text, reply_markup=_digest_kb(), parse_mode="HTML")
            _log(uid, "followup_digest_sent", {"date": day.isoformat()})
            delivered.append(uid)
        except Exception as e:
            system_logger.warning(f"Followup digest to {uid} failed: {e}")
    if not delivered:
        raise RuntimeError(f"digest delivery failed for all {len(pairs)} recipients")
    return delivered


def _seconds_until(hour: int, now: datetime | None = None) -> float:
    """Секунды до ближайшего hour:00:00 по локальным часам."""
    now = now or datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _digest_due_now(day: date, now: datetime | None = None) -> bool:
    """Сводка за day ещё не уходила и окно DAILY_SEND_HOUR уже наступило."""
    now = now or datetime.now()
    return (await _get_last_daily()) != day and now.hour >= DAILY_SEND_HOUR


async def _run_digest(bot, day: date) -> None:
    """Сводка за day: отправка + отметка last_daily; повтор при неудаче."""
    while True:
        try:
            delivered = await send_daily_digest(bot, day)
            await _set_last_daily(day)
            system_logger.info(f"Followup digest sent to {len(delivered)} recipient(s)")
            return
        except Exception as e:
            system_logger.error(f"Followup digest failed: {e}")
            if date.today() != day:
                return
            await asyncio.sleep(DIGEST_RETRY_DELAY)


async def reminders_loop(bot) -> None:
    """ЭТАП 2: фоновый планировщик утренней сводки (запускается из bot.py).

    При старте догоняет пропущенную за сегодня сводку, затем спит до
    ближайшего DAILY_SEND_HOUR:00. Живёт, пока жив процесс; bot.py отменяет
    задачу при выходе.
    """
    system_logger.info(
        f"Followup reminders loop started (daily at {DAILY_SEND_HOUR:02d}:00)"
    )
    day = date.today()
    if await _digest_due_now(day):
        await _run_digest(bot, day)
    while True:
        delay = _seconds_until(DAILY_SEND_HOUR)
        system_logger.info(f"Followup digest: next run in {delay / 3600:.1f} h")
        await asyncio.sleep(delay)
        await _run_digest(bot, date.today())


# ============================================================
# ЭТАП 3: ПРЕДВАРИТЕЛЬНЫЕ НАПОМИНАНИЯ «ЗА 10/5/2 МИНУТЫ»
# ============================================================
# upcoming_loop(bot) — второй фоновый сканер (запускается из bot.py рядом
# с reminders_loop): каждые PRE_SCAN_SECONDS берёт активные напоминания с
# точным временем (due_at) и, как только до события остаётся не больше N
# минут (N из PRE_REMIND_MINUTES), присылает «⏰ Через N минут…». Отправки
# фиксируются в followup_reminders — повторные сканы и рестарты дублей не
# дают; неудавшаяся отправка повторится на следующем скане. Уже наступившие
# события и выполненные дела не напоминаем.


async def upcoming_items(now: datetime | None = None) -> list[dict]:
    """Активные напоминания с точным временем в горизонте PRE_REMIND_MINUTES."""
    return await followups_db.upcoming(now, within_seconds=max(PRE_REMIND_MINUTES) * 60)


def _offsets_hint() -> str:
    """«10, 5 и 2 минуты» из PRE_REMIND_MINUTES (подсказка при сохранении)."""
    *head, last = sorted(PRE_REMIND_MINUTES, reverse=True)
    return f"{', '.join(map(str, head))} и {last} {_minutes_word(last)}"


def render_upcoming(item: dict, minutes_left: int, today: date | None = None) -> str:
    """Текст предварительного напоминания: «⏰ Через N минут: <текст>»."""
    today = today or date.today()
    lines = [f"⏰ <b>Через {minutes_left} {_minutes_word(minutes_left)}:</b> "
             f"{html.escape(str(item.get('text', '')))}"]
    due_at = _parse_dt_local(item.get("due_at"))
    if due_at:
        lines.append(f"📅 {_fmt_moment(due_at, today)}")
    lines.append("\nОтметить выполненным — «⬅️ Открыть напоминания».")
    return "\n".join(lines)


async def send_upcoming_reminders(bot, now: datetime | None = None) -> int:
    """Сканирует окно и рассылает ещё не отправленные «за N минут».

    Возвращает число отправленных сообщений. Журнал отмечается ПОСЛЕ
    успешной доставки: неудавшаяся отправка повторится на следующем скане.
    Если событие создали «в последнюю минуту» и часть интервалов ещё не
    уходила, недоставленные уходят разом — от большего интервала к меньшему.
    """
    now = now or datetime.now()
    sent = 0
    for it in await upcoming_items(now):
        fid, uid = it["id"], it["user_id"]
        due_at = _parse_dt_local(it.get("due_at"))
        if due_at is None:
            continue
        for offset in sorted(PRE_REMIND_MINUTES, reverse=True):
            if due_at - now > timedelta(minutes=offset):
                continue
            if await followups_db.reminder_sent(fid, offset):
                continue
            minutes_left = max(1, math.ceil((due_at - now).total_seconds() / 60))
            try:
                await bot.send_message(
                    uid, render_upcoming(it, minutes_left),
                    reply_markup=_digest_kb(), parse_mode="HTML",
                )
            except Exception as e:
                system_logger.warning(
                    f"Upcoming reminder #{fid} (-{offset} min) to {uid} failed: {e}"
                )
                continue
            await followups_db.mark_reminder_sent(fid, offset)
            _log(uid, "followup_upcoming_sent", {"id": fid, "minutes": offset})
            sent += 1
    return sent


async def upcoming_loop(bot) -> None:
    """ЭТАП 3: фоновый сканер предварительных напоминаний (запускается из bot.py).

    Первый скан — сразу при старте (догон пропущенного за время простоя,
    журналы не дадут дублей), далее каждые PRE_SCAN_SECONDS. Живёт, пока
    жив процесс; bot.py отменяет задачу при выходе.
    """
    offsets = "/".join(f"-{m} min" for m in sorted(PRE_REMIND_MINUTES, reverse=True))
    system_logger.info(f"Followup pre-event scanner started (reminders at {offsets})")
    while True:
        try:
            await send_upcoming_reminders(bot)
        except Exception as e:
            system_logger.error(f"Upcoming reminders scan failed: {e}")
        await asyncio.sleep(PRE_SCAN_SECONDS)


# ============================================================
# КЛАВИАТУРЫ ЭКРАНОВ
# ============================================================
def _main_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="➕ Новое напоминание", callback_data="fup:new")],
        [types.InlineKeyboardButton(text="🗂 Выполненные", callback_data="fup:done_list")],
        [types.InlineKeyboardButton(text="❌ Закрыть", callback_data="fup:cancel")],
    ])


def _active_kb(items: list[dict]) -> types.InlineKeyboardMarkup:
    """Кнопка «✅ Выполнить» под каждым напоминанием — в порядке показа на экране."""
    rows = [[types.InlineKeyboardButton(text="✅ Выполнить", callback_data=f"fup:done:{it['id']}")]
            for it in items[:ACTIVE_LIMIT]]
    rows.append([types.InlineKeyboardButton(text="➕ Новое напоминание", callback_data="fup:new")])
    rows.append([types.InlineKeyboardButton(text="🗂 Выполненные", callback_data="fup:done_list"),
                 types.InlineKeyboardButton(text="❌ Закрыть", callback_data="fup:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _done_kb(items: list[dict]) -> types.InlineKeyboardMarkup:
    rows = [[types.InlineKeyboardButton(text="↩️ Вернуть", callback_data=f"fup:undone:{it['id']}"),
             types.InlineKeyboardButton(text="🗑 Удалить", callback_data=f"fup:del:{it['id']}")]
            for it in items[:DONE_LIMIT]]
    rows.append([types.InlineKeyboardButton(text="⬅️ К напоминаниям", callback_data="fup:list"),
                 types.InlineKeyboardButton(text="❌ Закрыть", callback_data="fup:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _prompt_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="fup:cancel")],
    ])


def _item_id(data: str | None) -> int | None:
    """'fup:done:12' → 12; мусор → None."""
    try:
        return int(str(data).rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _is_exit(text: str) -> bool:
    # это выход, если текст — кнопка меню (кроме самой «⏰ Напоминания») или слово отмены
    return text in MENU_BUTTONS or text.strip().lower() in EXIT_WORDS


def _log(user_id: int, event: str, payload: dict) -> None:
    dialog_logger.info(json.dumps({
        "event": event,
        "user_id": user_id,
        **payload,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False))


async def _edit(callback: types.CallbackQuery, text: str, kb: types.InlineKeyboardMarkup | None) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        system_logger.warning(f"Followup edit failed: {e}")
        try:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
        except Exception as e2:
            system_logger.warning(f"Followup answer failed: {e2}")


# ============================================================
# КНОПКА МЕНЮ И ЭКРАНЫ СПИСКОВ
# ============================================================
@router.message(F.text == FOLLOWUP_BUTTON)
async def button_followup(message: types.Message, state: FSMContext):
    # Регистрируется ДО FSM-хендлеров: повторное нажатие кнопки во время ввода
    # перезапускает экран, а не ломает сценарий.
    await state.clear()
    _log(message.from_user.id, "followup_open", {})
    items = await get_active(message.from_user.id)
    await message.answer(
        _render_list(items),
        reply_markup=_main_kb() if not items else _active_kb(items),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "fup:list")
async def callback_list(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    items = await get_active(callback.from_user.id)
    await _edit(callback, _render_list(items), _active_kb(items) if items else _main_kb())
    await callback.answer()


@router.callback_query(F.data == "fup:done_list")
async def callback_done_list(callback: types.CallbackQuery):
    items = await get_done(callback.from_user.id)
    _log(callback.from_user.id, "followup_done_view", {"count": len(items)})
    await _edit(callback, _render_done(items), _done_kb(items))
    await callback.answer()


@router.callback_query(F.data == "fup:cancel")
async def callback_cancel(callback: types.CallbackQuery, state: FSMContext):
    _log(callback.from_user.id, "followup_cancel", {})
    await state.clear()
    await _edit(callback, "❌ Напоминания закрыты.", None)
    await callback.answer()


@router.callback_query(F.data == "fup:new")
async def callback_new(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(FollowUpState.waiting_for_text)
    await _edit(
        callback,
        "⏰ <b>Новое напоминание</b>\n\n"
        "Опишите задачу одним сообщением, например:\n"
        "<i>Клиент @username, модель 6S4P, позвонить через 3 дня</i>\n\n"
        "Срок пойму из слов «через 3 дня», «завтра», «25.09». "
        "Без срока — тоже сохраню (в группе «Без срока»).",
        _prompt_kb(),
    )
    await callback.answer()


# ---------- отметки: done / undone / delete ----------
@router.callback_query(F.data.startswith("fup:done:"))
async def callback_done(callback: types.CallbackQuery):
    item_id = _item_id(callback.data)
    if item_id is None or not await mark_done(callback.from_user.id, item_id):
        await callback.answer("Напоминание не найдено", show_alert=True)
        return
    _log(callback.from_user.id, "followup_done", {"id": item_id})
    items = await get_active(callback.from_user.id)
    await _edit(callback, _render_list(items), _active_kb(items) if items else _main_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("fup:undone:"))
async def callback_undone(callback: types.CallbackQuery):
    item_id = _item_id(callback.data)
    if item_id is None or not await reopen(callback.from_user.id, item_id):
        await callback.answer("Напоминание не найдено", show_alert=True)
        return
    _log(callback.from_user.id, "followup_reopen", {"id": item_id})
    items = await get_done(callback.from_user.id)
    await _edit(callback, _render_done(items), _done_kb(items))
    await callback.answer()


@router.callback_query(F.data.startswith("fup:del:"))
async def callback_del(callback: types.CallbackQuery):
    item_id = _item_id(callback.data)
    if item_id is None or not await delete_followup(callback.from_user.id, item_id):
        await callback.answer("Напоминание не найдено", show_alert=True)
        return
    _log(callback.from_user.id, "followup_delete", {"id": item_id})
    items = await get_done(callback.from_user.id)
    await _edit(callback, _render_done(items), _done_kb(items))
    await callback.answer()


# ============================================================
# FSM: ВВОД ТЕКСТА НАПОМИНАНИЯ
# ============================================================
@router.message(FollowUpState.waiting_for_text, F.text)
async def process_followup_text(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw == FOLLOWUP_BUTTON:      # повторная кнопка «⏰ Напоминания» — перерисовать экран
        await state.clear()
        await button_followup(message, state)
        return
    if _is_exit(raw):               # слово отмены или другая кнопка меню
        _log(message.from_user.id, "followup_cancel", {"via": "text"})
        await state.clear()
        await message.answer("❌ Напоминания закрыты. Выберите раздел в меню.")
        return
    due_at = parse_due_at(raw)
    due = due_at.date() if due_at else parse_due(raw)
    item = await add_followup(message.from_user.id, raw, due, due_at)
    _log(message.from_user.id, "followup_created",
         {"id": item["id"], "due": item["due"], "due_at": item["due_at"]})
    await state.clear()
    if due_at:
        when = (f"Срок: <b>{_fmt_moment(due_at, date.today())}</b>. "
                f"Пришлю напоминание за {_offsets_hint()}.")
    elif due:
        when = f"Срок: <b>{_fmt_due(due, date.today())}</b>."
    else:
        when = "Срок не указан — напоминание в группе «Без срока»."
    items = await get_active(message.from_user.id)
    await message.answer(
        f"✅ <b>Напоминание №{item['id']} сохранено.</b>\n{when}\n\n" + _render_list(items),
        reply_markup=_active_kb(items) if items else _main_kb(),
        parse_mode="HTML",
    )


@router.message(FollowUpState.waiting_for_text)
async def process_followup_not_text(message: types.Message):
    await message.answer(
        "Отправьте напоминание текстом одним сообщением, например:\n"
        "<i>Клиент @username, модель 6S4P, позвонить через 3 дня</i>",
        parse_mode="HTML",
    )





