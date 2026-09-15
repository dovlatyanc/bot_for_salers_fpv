"""
Модуль «🎯 Сценарии» — готовые шпаргалки менеджера под типовых клиентов.

  «🎯 Сценарии» (reply) — список сценариев;
  scn:<id>              — экран сценария: портрет клиента, рекомендуемая модель,
                          преимущества, вопросы-ответы, сопутствующие товары;
  scn:list              — возврат к списку сценариев;
  scn:full              — все сценарии одним сообщением (для пересылки/печати);
  model:<код>           — НЕ обрабатывается здесь: кнопки «📦 Карточка <модель>»
                          перехватывает роутер menu (зарегистрирован раньше).

Контент сценариев — data/scenarios.py; общие ресурсы (бот, логгеры) — config.
"""

import json
from datetime import datetime

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import dialog_logger, system_logger
from data import MODELS, SCENARIOS, SCENARIO_ORDER
from utils import answer_long

router = Router(name="scenarios")


# ---------- тексты ----------

def _spec_line(code: str) -> str:
    m = MODELS[code]
    return (f"{m['voltage']} · {m['mah']} мА·ч · {m['kg']} кг · "
            f"{m['cont']}/{m['peak']} А · {m['connector']}")


def _scenario_text(sid: str, html: bool = True) -> str:
    """Экран одного сценария.

    html=False — plain-текст для полной сводки: answer_long шлёт части без
    parse_mode, и HTML-теги могли бы разрезаться на границе частей.
    """
    sc = SCENARIOS[sid]
    b = (lambda s: f"<b>{s}</b>") if html else (lambda s: s)
    lines = [
        f"{sc['emoji']} {b(sc['title'])} — шпаргалка менеджера",
        "",
        b("👤 Портрет клиента:"),
        sc["client"],
        "",
        b("🔋 Рекомендуем:"),
    ]
    lines += [f"  • {b(code)} — {_spec_line(code)}" for code in sc["recommend"]]
    lines += ["", b("✅ Ключевые преимущества для этого клиента:")]
    lines += [f"  • {w}" for w in sc["why"]]
    lines += ["", b("💬 Типовые вопросы и ответы:")]
    for q, a in sc["qa"]:
        lines += [f"  ❓ {q}", f"  ➡️ {a}"]
    if sc["extras"]:
        lines += ["", b("🛒 Сопутствующие товары:")]
        lines += [f"  • {b(name)} — {why}" for name, why in sc["extras"]]
    return "\n".join(lines)


LIST_TEXT = (
    "🎯 <b>Сценарии продаж</b>\n\n"
    "Готовые шпаргалки под типовых клиентов: портрет, рекомендуемая модель, "
    "преимущества, ответы на типовые вопросы и сопутствующие товары.\n\n"
    "Выберите сценарий:"
)


# ---------- клавиатуры ----------

def _list_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{SCENARIOS[sid]['emoji']} {SCENARIOS[sid]['title']}",
            callback_data=f"scn:{sid}",
        )]
        for sid in SCENARIO_ORDER
    ]
    rows.append([InlineKeyboardButton(text="📄 Всё одним сообщением", callback_data="scn:full")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _scenario_kb(sid: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📦 Карточка {code}", callback_data=f"model:{code}")]
        for code in SCENARIOS[sid]["recommend"]
    ]
    rows.append([InlineKeyboardButton(text="⬅️ К списку сценариев", callback_data="scn:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- логирование ----------

def _log_cb(callback: types.CallbackQuery, event: str, scenario: str) -> None:
    dialog_logger.info(json.dumps({
        'event': event,
        'user_id': callback.from_user.id,
        'scenario': scenario,
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))


# ---------- обработчики ----------

@router.message(F.text == "🎯 Сценарии")
async def button_scenarios(message: types.Message):
    dialog_logger.info(json.dumps({
        'event': 'scenarios_open',
        'user_id': message.from_user.id,
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))
    await message.answer(LIST_TEXT, reply_markup=_list_kb(), parse_mode="HTML")


@router.callback_query(F.data == "scn:list")
async def callback_scenario_list(callback: types.CallbackQuery):
    _log_cb(callback, 'scenario_list', 'list')
    try:
        await callback.message.edit_text(LIST_TEXT, reply_markup=_list_kb(), parse_mode="HTML")
    except Exception as e:
        system_logger.warning(f"Scenarios list edit failed: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("scn:"))
async def callback_scenario(callback: types.CallbackQuery):
    sid = callback.data.split(":", 1)[1]
    if sid != "full" and sid not in SCENARIOS:
        await callback.answer("Сценарий не найден", show_alert=True)
        return

    if sid == "full":
        _log_cb(callback, 'scenario_full', 'all')
        sep = "\n\n" + "—" * 30 + "\n\n"
        await answer_long(callback.message, sep.join(_scenario_text(s, html=False) for s in SCENARIO_ORDER))
        await callback.answer()
        return

    _log_cb(callback, 'scenario_view', sid)
    text = _scenario_text(sid)
    if len(text) <= 4000:  # лимит edit_text 4096, у одного сценария запас
        try:
            await callback.message.edit_text(
                text, reply_markup=_scenario_kb(sid), parse_mode="HTML"
            )
        except Exception as e:
            system_logger.warning(f"Scenario view edit failed: {e}")
            await answer_long(callback.message, text)
    else:
        await answer_long(callback.message, text)
    await callback.answer()