"""
Модуль «🎯 Сценарии» — готовые шпаргалки менеджера под типовых клиентов.

  «🎯 Сценарии» (reply) — список сценариев;
  scn:<id>              — экран сценария: портрет клиента, рекомендуемая модель,
                          преимущества, вопросы-ответы, сопутствующие товары;
  scn:list              — возврат к списку сценариев;
  scn:full              — все сценарии одним сообщением (для пересылки/печати);
  model:<код>           — НЕ обрабатывается здесь: кнопки «📦 Карточка <модель>»
                          перехватывает роутер menu (зарегистрирован раньше).

Контент сценариев — data/scenarios.py; общие UI-хелперы — utils/ui.py.
"""

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from data import MODELS, SCENARIOS, SCENARIO_ORDER
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

router = Router(name="scenarios")


def _spec_line(code: str) -> str:
    """Строка ТТХ модели. Только цифры/буквы/· — экранирование не нужно."""
    m = MODELS[code]
    return (f"{m['voltage']} · {m['mah']} мА·ч · {m['kg']} кг · "
            f"{m['cont']}/{m['peak']} А · {m['connector']}")


# ============================================================
# ТЕКСТЫ
# ============================================================
def _scenario_text(sid: str, with_html: bool = True) -> str:
    """Экран одного сценария.

    with_html=False — plain-текст для полной сводки: answer_long шлёт части
    без parse_mode, и HTML-теги могли бы разрезаться на границе частей.
    В plain-режиме данные НЕ экранируются (иначе < превратится в &lt;).
    В HTML-режиме все данные экранируются, чтобы <, >, & не ломали разметку.
    """
    sc = SCENARIOS[sid]
    e = _esc if with_html else (lambda s: str(s))
    b = (lambda s: f"<b>{s}</b>") if with_html else (lambda s: s)

    lines = [
        f"{sc.get('emoji', '')} {b(e(sc.get('title', sid)))} — шпаргалка менеджера",
        "",
        b("👤 Портрет клиента:"),
        e(sc.get("client", "")),
        "",
        b("🔋 Рекомендуем:"),
    ]
    for code in sc.get("recommend", []):
        lines.append(f"  • {b(e(code))} — {_spec_line(code)}")

    lines += ["", b("✅ Ключевые преимущества для этого клиента:")]
    for w in sc.get("why", []):
        lines.append(f"  • {e(w)}")

    qa = sc.get("qa", [])
    if qa:
        lines += ["", b("💬 Типовые вопросы и ответы:")]
        for q, a in qa:
            lines += [f"  ❓ {e(q)}", f"  ➡️ {e(a)}"]

    extras = sc.get("extras", [])
    if extras:
        lines += ["", b("🛒 Сопутствующие товары:")]
        for name, why in extras:
            lines.append(f"  • {b(e(name))} — {e(why)}")

    return "\n".join(lines)


LIST_TEXT = (
    "🎯 <b>Сценарии продаж</b>\n\n"
    "Готовые шпаргалки под типовых клиентов: портрет, рекомендуемая модель, "
    "преимущества, ответы на типовые вопросы и сопутствующие товары.\n\n"
    "Выберите сценарий:"
)


# ============================================================
# КЛАВИАТУРЫ
# ============================================================
def _ordered_ids() -> list[str]:
    """SCENARIO_ORDER, отфильтрованный по фактическому наличию в SCENARIOS."""
    return ordered_ids(
        SCENARIO_ORDER, SCENARIOS,
        warn_format="SCENARIO_ORDER: нет в SCENARIOS — {missing}",
    )


def _list_kb() -> InlineKeyboardMarkup:
    rows = []
    for sid in _ordered_ids():
        sc = SCENARIOS[sid]
        rows.append([InlineKeyboardButton(
            text=f"{sc.get('emoji', '')} {sc.get('title', sid)}",
            callback_data=f"scn:{sid}",
        )])
    rows.append([InlineKeyboardButton(text="📄 Всё одним сообщением", callback_data="scn:full")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _scenario_kb(sid: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📦 Карточка {code}", callback_data=f"model:{code}")]
        for code in SCENARIOS[sid].get("recommend", [])
    ]
    rows.append([InlineKeyboardButton(text="⬅️ К списку сценариев", callback_data="scn:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
# ЛОГИРОВАНИЕ (ключи событий — как раньше; вывод общий в utils.ui.log_event)
# ============================================================
def _log(user_id: int, event: str, scenario: str) -> None:
    log_event(user_id, event, {"scenario": scenario})


def _log_cb(callback: types.CallbackQuery, event: str, scenario: str) -> None:
    _log(callback.from_user.id, event, scenario)


# ============================================================
# ХЕЛПЕРЫ
# ============================================================
async def _show_list(message: types.Message, *, edit: bool = False) -> None:
    """Показать список сценариев: новым сообщением или редактированием."""
    await show_screen(
        message, LIST_TEXT, _list_kb(), edit=edit,
        warn_prefix="Scenarios list edit failed",
    )


async def _scenario_full(callback: types.CallbackQuery) -> None:
    """scn:full — все сценарии одним сообщением (plain-text, без parse_mode)."""
    _log_cb(callback, "scenario_full", "all")
    text = SEPARATOR.join(
        _scenario_text(sid, with_html=False) for sid in _ordered_ids()
    )
    await safe_answer_long(callback.message, text)


# ============================================================
# ОБРАБОТЧИКИ
# ============================================================
@router.message(F.text == "🎯 Сценарии")
async def button_scenarios(message: types.Message, state: FSMContext):
    """«🎯 Сценарии» — список сценариев."""
    # выход из активного FSM-сценария — иначе «зависшее» состояние
    # перехватывало бы следующие тексты пользователя
    await state.clear()
    _log(message.from_user.id, "scenarios_open", "list")
    await _show_list(message, edit=False)


@router.callback_query(F.data == "scn:list")
async def callback_scenario_list(callback: types.CallbackQuery):
    """scn:list — возврат к списку сценариев."""
    _log_cb(callback, "scenario_list", "list")
    await _show_list(callback.message, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("scn:"))
async def callback_scenario(callback: types.CallbackQuery):
    """scn:<id> — экран сценария. scn:list и scn:full обрабатываются явно,
    даже если порядок регистрации хендлеров изменится."""
    sid = callback.data.split(":", 1)[1]

    if sid == "list":
        await callback_scenario_list(callback)
        return
    if sid == "full":
        await _scenario_full(callback)
        await callback.answer()
        return
    if sid not in SCENARIOS:
        await callback.answer("Сценарий не найден", show_alert=True)
        return

    _log_cb(callback, "scenario_view", sid)
    await edit_or_send(
        callback.message, _scenario_text(sid),
        reply_markup=_scenario_kb(sid),
        warn_prefix="Scenario view edit failed",
    )
    await callback.answer()