"""
Модуль мульти-фильтра «🔎 Поиск» (вынесено из bot.py без изменения логики и текстов):
  «🔎 Поиск»  — экран фильтров (напряжение / ёмкость / платформа);
  srch:...    — переключение фильтров, показ результатов, сброс, возврат;
  srchm:...   — карточка модели из результатов с возвратом к ним.

Состояние фильтров stateless — передаётся в callback_data каждой кнопки.
Общие ресурсы берутся из config/utils/keyboards — импорта bot.py нет.
"""

import json
from datetime import datetime

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import bot, dialog_logger, system_logger
from data import FILTER_LABELS
from keyboards import SEARCH_HEADER_TEXT, _build_search_kb
from utils import _apply_filters, get_row, send_model_card

router = Router(name="search")


@router.callback_query(F.data.startswith("srchm:"))
async def callback_search_model(callback: types.CallbackQuery):
    # Формат callback_data (см. callback_search, action == "show"):
    #   srchm:{code}:d={d}:v={v}:c={c}
    parts = callback.data[len("srchm:"):].split(":")
    model_code = parts[0]
    drone, voltage, cap_class = "all", "all", "all"
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key == "d":
            drone = value
        elif key == "v":
            voltage = value
        elif key == "c":
            cap_class = value

    row = get_row(model_code)
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="⬅️ К результатам поиска",
                callback_data=f"srch:back:d={drone}:v={voltage}:c={cap_class}",
            )],
            [InlineKeyboardButton(
                text=f"⬅️ Ко всем моделям {row}",
                callback_data=f"row:{row}",
            )],
        ]
    )
    # MODIFIED: карточка через send_model_card (фото + подпись, если фото есть)
    await send_model_card(callback, model_code, back_kb, bot)

    await callback.answer()

# === МУЛЬТИ-ФИЛЬТР «🔎 Поиск» ===
# NEW: точка входа в мульти-фильтр
@router.message(F.text == "🔎 Поиск")
async def button_search(message: types.Message, state: FSMContext):
    # выход из активного FSM-сценария (см. button_ttx в menu.py)
    await state.clear()
    # MODIFIED: заголовки секций перенесены из label-кнопок в текст сообщения (HTML)
    await message.answer(
        SEARCH_HEADER_TEXT,
        reply_markup=_build_search_kb(),
        parse_mode="HTML",
    )

# MODIFIED: комментарий обновлён — каскад "flt:" удалён; префикс "srch:" уникален
# относительно "sel:" (экспресс-подбор «🧠») и "model:" (карточки); состояние фильтров
# передаётся в callback_data каждой кнопки (stateless).
@router.callback_query(F.data.startswith("srch:"))
async def callback_search(callback: types.CallbackQuery):
    # Форматы callback_data (см. _build_search_kb):
    #   srch:v={v}:d={d}:c={c}       — выбор напряжения
    #   srch:c={c}:d={d}:v={v}       — выбор класса ёмкости
    #   srch:d={d}:v={v}:c={c}       — выбор платформы
    #   srch:show:d={d}:v={v}:c={c}  — показать результаты
    #   srch:reset                   — сброс всех фильтров в "all"
    #   srch:back:d={d}:v={v}:c={c}  — возврат к экрану фильтров
    drone, voltage, cap_class = "all", "all", "all"
    action = None
    for part in callback.data[len("srch:"):].split(":"):
        if "=" in part:
            key, value = part.split("=", 1)
            if key == "d":
                drone = value
            elif key == "v":
                voltage = value
            elif key == "c":
                cap_class = value
        else:
            action = part

    if action == "reset":
        drone = voltage = cap_class = "all"

    filters = {"drone": drone, "voltage": voltage, "cap_class": cap_class}

    # ЛОГИРОВАНИЕ: действие мульти-фильтра
    dialog_logger.info(json.dumps({
        'event': 'search_action',
        'user_id': callback.from_user.id,
        'username': callback.from_user.username or 'unknown',
        'action': action or 'filter',
        'filters': filters,
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))

    def _disp(dict_key: str, value: str) -> str:
        # "all" — псевдозначение, которого нет в FILTER_LABELS["drone"]
        if value == "all":
            return "Любая" if dict_key == "drone" else "Все"
        return FILTER_LABELS[dict_key].get(value, value)

    if action == "show":
        matches = _apply_filters(drone=drone, voltage=voltage, cap_class=cap_class)
        text = (
            f"🔎 Поиск АКБ — результаты\n\n"
            f"Платформа: {_disp('drone', drone)}\n"
            f"Напряжение: {_disp('voltage', voltage)}\n"
            f"Ёмкость: {_disp('cap_class', cap_class)}\n\n"
            f"Найдено моделей: {len(matches)}\n\n"
            "Выберите модель:"
        )
        buttons = [
            [InlineKeyboardButton(
                text=f"{code} | {m['mah']}мАч | {m['kg']}кг",
                callback_data=f"srchm:{code}:d={drone}:v={voltage}:c={cap_class}",  # MODIFIED: возврат к результатам поиска вместо потока ТТХ
            )]
            for code, m in matches.items()
        ]
        buttons.append([InlineKeyboardButton(
            text="⬅️ К фильтрам",
            callback_data=f"srch:back:d={drone}:v={voltage}:c={cap_class}",
        )])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    else:
        # filter / reset / back — экран фильтров с актуальным состоянием
        # MODIFIED: заголовки секций в тексте (HTML), как в button_search
        text = SEARCH_HEADER_TEXT
        kb = _build_search_kb(drone=drone, voltage=voltage, cap_class=cap_class)

    try:
        # MODIFIED: parse_mode="HTML" — текст экрана фильтров содержит <b>-теги
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        system_logger.warning(f"Search edit failed: {e}")

    await callback.answer()
