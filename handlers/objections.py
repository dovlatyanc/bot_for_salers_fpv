"""
Модуль «💬 Возражения» — готовые скрипты отработки возражений.

  «💬 Возражения» (reply) — список возражений;
  obj:<id>               — экран возражения: как звучит, «коротко»,
                           «подробнее», «чего не говорить», альтернатива;
  obj:list               — возврат к списку;
  obj:full               — все возражения одним сообщением (для печати);
  obj:stop               — только стоп-возражения (5 пунктов из стоп-листа).

Контент — data/objections.py; общие UI-хелперы — utils/ui.py.
"""

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from data.objections import OBJECTIONS, OBJECTION_ORDER
from utils.ui import (
    EDIT_SAFE_LIMIT,
    SEPARATOR,
    edit_or_send,
    esc as _esc,
    log_event,
    ordered_ids,
    safe_answer_long,
    show_screen,
)

router = Router(name="objections")


def _objection_text(oid: str, with_html: bool = True) -> str:
    """Экран одного возражения.

    with_html=False — plain-текст для полной сводки (answer_long).
    В plain-режиме данные НЕ экранируются; в HTML — экранируются,
    чтобы <, >, & не ломали разметку.
    """
    obj = OBJECTIONS[oid]
    e = _esc if with_html else (lambda s: str(s))
    b = (lambda s: f"<b>{s}</b>") if with_html else (lambda s: s)

    lines = [
        f"{obj.get('emoji', '')} {b(e(obj.get('title', oid)))}",
        "",
        b("👤 Как звучит:"),
        e(obj.get("client", "")),
        "",
        b("⚡ Что говорить (коротко):"),
        e(obj.get("short", "")),
    ]

    detail = obj.get("detail", "")
    if detail:
        lines += ["", b("📖 Если спросят подробнее:"), e(detail)]

    dont = obj.get("dont", [])
    if dont:
        lines += ["", b("🚫 Чего не говорить:")]
        for d in dont:
            lines.append(f"  • {e(d)}")

    alt = obj.get("alt", "")
    if alt:
        lines += ["", b("🔄 Альтернатива:"), e(alt)]

    if obj.get("stop"):
        lines += ["", b("🛑 Это стоп-возражение: сегодня не продаём.")]

    return "\n".join(lines)


LIST_TEXT = (
    "💬 <b>Возражения</b>\n\n"
    "Готовые скрипты отработки типовых возражений: как звучит, "
    "«коротко», «подробнее», «чего не говорить», альтернатива.\n\n"
    "Выберите возражение или посмотрите только стоп-лист:"
)

STOP_LIST_TEXT = (
    "🛑 <b>Стоп-лист: пять случаев, когда не продаём сегодня</b>\n\n"
    "1. Не подтверждено напряжение регуляторов.\n"
    "2. Задача — рывок (перехват, ПВО, гонки).\n"
    "3. Бюджет массы под батарею на 10\" меньше 1,3 кг.\n"
    "4. Клиент назвал ток на подъёме выше пикового тока модели, "
    "которая влезает по массе (2P — 90 А, 3P — 135 А).\n"
    "5. Нет балансного ЗУ с режимом Li-Ion, и клиент отказался "
    "от допродажи.\n\n"
    "Нажмите на возражение ниже — покажу скрипт."
)


# ============================================================
# КЛАВИАТУРЫ
# ============================================================
def _ordered_ids(only_stop: bool = False) -> list[str]:
    """OBJECTION_ORDER, отфильтрованный по наличию и (опционально) по stop."""
    return ordered_ids(
        OBJECTION_ORDER, OBJECTIONS, only_stop=only_stop,
        warn_format="OBJECTION_ORDER: нет в OBJECTIONS — {missing}",
    )


def _list_kb() -> InlineKeyboardMarkup:
    rows = []
    for oid in _ordered_ids():
        obj = OBJECTIONS[oid]
        prefix = "🛑 " if obj.get("stop") else ""
        rows.append([InlineKeyboardButton(
            text=f"{prefix}{obj.get('emoji', '')} {obj.get('title', oid)}",
            callback_data=f"obj:{oid}",
        )])
    rows.append([
        InlineKeyboardButton(text="🛑 Только стоп-лист", callback_data="obj:stop"),
        InlineKeyboardButton(text="📄 Всё одним", callback_data="obj:full"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _objection_kb(oid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К возражениям", callback_data="obj:list")],
    ])


def _stop_kb() -> InlineKeyboardMarkup:
    rows = []
    for oid in _ordered_ids(only_stop=True):
        obj = OBJECTIONS[oid]
        rows.append([InlineKeyboardButton(
            text=f"🛑 {obj.get('emoji', '')} {obj.get('title', oid)}",
            callback_data=f"obj:{oid}",
        )])
    rows.append([
        InlineKeyboardButton(text="⬅️ Все возражения", callback_data="obj:list"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
# ЛОГИРОВАНИЕ (ключи событий — как раньше; вывод общий в utils.ui.log_event)
# ============================================================
def _log(user_id: int, event: str, objection: str) -> None:
    log_event(user_id, event, {"objection": objection})


def _log_cb(callback: types.CallbackQuery, event: str, objection: str) -> None:
    _log(callback.from_user.id, event, objection)


# ============================================================
# ХЕЛПЕРЫ
# ============================================================
async def _show_list(message: types.Message, *, edit: bool = False) -> None:
    await show_screen(
        message, LIST_TEXT, _list_kb(), edit=edit,
        warn_prefix="Objections list edit failed",
    )


async def _show_stop(message: types.Message, *, edit: bool = False) -> None:
    await show_screen(
        message, STOP_LIST_TEXT, _stop_kb(), edit=edit,
        warn_prefix="Objections stop edit failed",
    )


async def _objection_full(callback: types.CallbackQuery) -> None:
    _log_cb(callback, "objections_full", "all")
    text = SEPARATOR.join(
        _objection_text(oid, with_html=False) for oid in _ordered_ids()
    )
    await safe_answer_long(callback.message, text)


# ============================================================
# ОБРАБОТЧИКИ
# ============================================================
@router.message(F.text == "💬 Возражения")
async def button_objections(message: types.Message):
    """«💬 Возражения» — список возражений."""
    _log(message.from_user.id, "objections_open", "list")
    await _show_list(message, edit=False)


@router.callback_query(F.data == "obj:list")
async def callback_objections_list(callback: types.CallbackQuery):
    """obj:list — возврат к списку возражений."""
    _log_cb(callback, "objections_list", "list")
    await _show_list(callback.message, edit=True)
    await callback.answer()


@router.callback_query(F.data == "obj:stop")
async def callback_objections_stop(callback: types.CallbackQuery):
    """obj:stop — только стоп-возражения."""
    _log_cb(callback, "objections_stop", "stop")
    await _show_stop(callback.message, edit=True)
    await callback.answer()


@router.callback_query(F.data == "obj:full")
async def callback_objections_full(callback: types.CallbackQuery):
    """obj:full — все возражения одним сообщением (plain-text)."""
    await _objection_full(callback)
    await callback.answer()


@router.callback_query(F.data.startswith("obj:"))
async def callback_objection(callback: types.CallbackQuery):
    """obj:<id> — экран возражения. obj:list/stop/full — обрабатываются
    явными хендлерами раньше; сюда не попадают. Защита от перестановки:
    если попали — вызываем соответствующий хелпер."""
    oid = callback.data.split(":", 1)[1]

    if oid == "list":
        await callback_objections_list(callback)
        return
    if oid == "stop":
        await callback_objections_stop(callback)
        return
    if oid == "full":
        await callback_objections_full(callback)
        return
    if oid not in OBJECTIONS:
        await callback.answer("Возражение не найдено", show_alert=True)
        return

    _log_cb(callback, "objection_view", oid)
    await edit_or_send(
        callback.message, _objection_text(oid),
        reply_markup=_objection_kb(oid),
        warn_prefix="Objection view edit failed",
    )
    await callback.answer()