from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)

from data import FILTER_LABELS, PLATFORMS, VOLTAGES, CAP_CLASSES
from utils import _apply_filters


#: Все reply-кнопки главного меню. frozenset — для проверок «это кнопка меню?».
#: Порядок в клавиатуре задаётся отдельно в MAIN_KEYBOARD_LAYOUT.
#: Каталог моделей («📊 ТТХ») и справочные разделы («🧠 Подбор», «🎯 Сценарии»,
#: «❓ FAQ», «💬 Возражения», «🛑 Стоп-лист», «📦 Памятка») в клавиатуре не
#: перечислены — каталог переехал в «📚 Шпаргалки» (cheat:ttx), справочные
#: разделы собраны там же (handlers/cheatsheets.py).
MAIN_BUTTONS = frozenset({
    "🔎 Поиск", "💰 Калькулятор", "🔧 Своя сборка",
    "📚 Шпаргалки", "🤖 AI-ассистент", "📝 Обратная связь",
    "⏰ Напоминания", "📊 История расчётов",
    "🛠 Конструктор дронов",
})

#: Кнопки, убранные из меню, но способные прийти с закэшированных клавиатур:
#: их тексты всё ещё считаются «кнопкой меню» при выходе из FSM-сценариев
#: (сами хендлеры сохранены — см. button_ttx в handlers/menu.py).
LEGACY_MAIN_BUTTONS = frozenset({"📊 ТТХ"})

#: Полное множество «кнопочных» текстов для зеркал выхода из FSM
#: (calculator/faq/followup/build, обратная связь в menu.py).
MENU_EXIT_BUTTONS = MAIN_BUTTONS | LEGACY_MAIN_BUTTONS

#: Раскладка главной клавиатуры. Сгруппирована по смыслу:
#:   подбор/расчёты → общение с клиентом → конструктор (3-й ряд, Этап 9).
MAIN_KEYBOARD_LAYOUT = (
    ("🔎 Поиск",          "💰 Калькулятор",     "🔧 Своя сборка",   "📚 Шпаргалки"),
    ("🤖 AI-ассистент",   "📝 Обратная связь",  "⏰ Напоминания",   "📊 История расчётов"),
    ("🛠 Конструктор дронов",),
)

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=label) for label in row]
        for row in MAIN_KEYBOARD_LAYOUT
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