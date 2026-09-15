from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)

from data import FILTER_LABELS, PLATFORMS, VOLTAGES, CAP_CLASSES
from utils import _apply_filters


MAIN_BUTTONS = {
    "📊 ТТХ", "🧠 Подбор", "🎯 Сценарии", "🔎 Поиск", "🤖 AI-ассистент", "🛑 Стоп-лист",
    "📦 Памятка", "🔧 Своя сборка", "📝 Обратная связь",
    "💰 Калькулятор", "📊 История расчётов", "⏰ Напоминания",
}

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 ТТХ"), KeyboardButton(text="🔎 Поиск"), KeyboardButton(text="🤖 AI-ассистент")],
        [KeyboardButton(text="🧠 Подбор"), KeyboardButton(text="🎯 Сценарии"), KeyboardButton(text="🔧 Своя сборка")],
        [KeyboardButton(text="💰 Калькулятор"), KeyboardButton(text="📊 История расчётов"), KeyboardButton(text="⏰ Напоминания")],
        [KeyboardButton(text="🛑 Стоп-лист"), KeyboardButton(text="📦 Памятка")],
        [KeyboardButton(text="📝 Обратная связь")],
    ],
    resize_keyboard=True,
)


def _build_search_kb(drone="all", voltage="all", cap_class="all") -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    def _mark(active: bool, label: str) -> str:
        return f"✅ {label}" if active else label

    for v in VOLTAGES + ["all"]:
        buttons.append([InlineKeyboardButton(
            text=_mark(voltage == v, FILTER_LABELS["voltage"][v]),
            callback_data=f"srch:v={v}:d={drone}:c={cap_class}",
        )])
    for c in CAP_CLASSES + ["all"]:
        buttons.append([InlineKeyboardButton(
            text=_mark(cap_class == c, FILTER_LABELS["cap_class"][c]),
            callback_data=f"srch:c={c}:d={drone}:v={voltage}",
        )])
    for d in PLATFORMS:
        buttons.append([InlineKeyboardButton(
            text=_mark(drone == d, FILTER_LABELS["drone"][d]),
            callback_data=f"srch:d={d}:v={voltage}:c={cap_class}",
        )])
    buttons.append([InlineKeyboardButton(
        text=_mark(drone == "all", "Все"),
        callback_data=f"srch:d=all:v={voltage}:c={cap_class}",
    )])

    if _apply_filters(drone=drone, voltage=voltage, cap_class=cap_class):
        buttons.append([InlineKeyboardButton(
            text="🔍 Показать результаты",
            callback_data=f"srch:show:d={drone}:v={voltage}:c={cap_class}",
        )])
    else:
        buttons.append([InlineKeyboardButton(text="Нет совпадений", callback_data="dummy")])

    buttons.append([InlineKeyboardButton(text="♻️ Сбросить фильтры", callback_data="srch:reset")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


SEARCH_HEADER_TEXT = (
    "🔎 Поиск АКБ\n\n"
    "Отметьте нужные параметры — покажу подходящие модели:\n\n"
    "⚡ <b>Напряжение</b>\n"
    "🔋 <b>Ёмкость</b>\n"
    "🚁 <b>Платформа</b>"
)