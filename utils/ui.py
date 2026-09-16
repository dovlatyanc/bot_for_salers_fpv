"""
Общие UI-хелперы статических разделов бота (сценарии, возражения).

Единый источник для кода, который раньше дублировался в
handlers/scenarios.py и handlers/objections.py:
  esc / EDIT_SAFE_LIMIT / SEPARATOR — экранирование и лимиты;
  log_event / log_cb                — события раздела в dialog-log (JSON);
  ordered_ids                       — порядок раздела, отфильтрованный по данным;
  show_screen                       — «edit или новое сообщение»;
  safe_answer_long                  — длинный текст частями без падения потока;
  edit_or_send                      — правка на месте с фолбэком на answer_long.

Поведение и тексты предупреждений в system-log сохранены 1:1 — см. вызовы.
"""

import html
import json
from datetime import datetime
from typing import Any, Mapping

from aiogram import types
from aiogram.types import InlineKeyboardMarkup

from config import dialog_logger, system_logger

from .utils import answer_long

#: Telegram: лимит edit_text — 4096 символов. Оставляем запас.
EDIT_SAFE_LIMIT = 4000

#: Разделитель частей полной сводки («всё одним сообщением»).
SEPARATOR = "\n\n" + "—" * 30 + "\n\n"


# ============================================================
# ЭКРАНИРОВАНИЕ
# ============================================================
def esc(value) -> str:
    """HTML-экранирование пользовательских данных (но не наших <b>-тегов)."""
    return html.escape(str(value), quote=False)


# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
def log_event(user_id: int, event: str, payload: Mapping[str, Any] | None = None) -> None:
    """Событие раздела в dialog-log — JSON-строкой (единый формат разделов)."""
    dialog_logger.info(json.dumps({
        "event": event,
        "user_id": user_id,
        **(dict(payload) if payload else {}),
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False))


def log_cb(
    callback: types.CallbackQuery,
    event: str,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """То же, что log_event, но user_id берётся из CallbackQuery."""
    log_event(callback.from_user.id, event, payload)


# ============================================================
# ПОРЯДОК РАЗДЕЛА
# ============================================================
def ordered_ids(
    order,
    items: Mapping[str, dict],
    *,
    only_stop: bool = False,
    warn_format: str | None = None,
) -> list[str]:
    """Порядок из data-модуля, отфильтрованный по фактическому наличию в
    справочнике (и, опционально, по флагу stop).

    warn_format — шаблон предупреждения в system-log для id из order,
    отсутствующих в items (например "SCENARIO_ORDER: нет в SCENARIOS — {missing}").
    """
    ids: list[str] = []
    missing: list[str] = []
    for oid in order:
        if oid not in items:
            missing.append(oid)
            continue
        if only_stop and not items[oid].get("stop"):
            continue
        ids.append(oid)
    if missing and warn_format:
        system_logger.warning(warn_format.format(missing=missing))
    return ids


# ============================================================
# ОТПРАВКА / РЕДАКТИРОВАНИЕ
# ============================================================
async def show_screen(
    message: types.Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    *,
    edit: bool,
    warn_prefix: str,
) -> None:
    """edit=True — отредактировать сообщение (ошибка — только warning, без
    отправки нового); edit=False — отправить новым сообщением. Всегда HTML."""
    if edit:
        try:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception as e:
            system_logger.warning(f"{warn_prefix}: {e}")
    else:
        await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


async def safe_answer_long(message: types.Message, text: str) -> None:
    """answer_long без падения потока: ошибку — в system-log."""
    try:
        await answer_long(message, text)
    except Exception as e:
        system_logger.warning(f"answer_long failed: {e}")


async def edit_or_send(
    message: types.Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    warn_prefix: str = "edit failed",
) -> None:
    """Показ текста на месте: edit_text, если влезает в лимит редактирования;
    при ошибке правки (или если текст длиннее лимита) — ответом частями."""
    if len(text) <= EDIT_SAFE_LIMIT:
        try:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
            return
        except Exception as e:
            system_logger.warning(f"{warn_prefix}: {e}")
    await safe_answer_long(message, text)