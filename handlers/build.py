import re

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import bot, system_logger
from keyboards import MENU_EXIT_BUTTONS
from states import BuildState

router = Router()

BUILD_BUTTON = "🔧 Своя сборка"
# Кнопки меню (+ легаси «📊 ТТХ»), по которым можно выйти из ввода (сама
# «🔧 Своя сборка» — перезапуск: её перехватывает start_build, зарегистрированный
# раньше FSM). Зеркало MENU_EXIT_BUTTONS — приём calculator/faq/followup.
MENU_BUTTONS = frozenset(MENU_EXIT_BUTTONS) - {BUILD_BUTTON}
EXIT_WORDS = frozenset({"отмена", "cancel", "❌", "❌ отмена"})


def _is_exit(text: str) -> bool:
    # это выход, если текст — кнопка меню (кроме самой «🔧 Своя сборка») или слово отмены
    return text in MENU_BUTTONS or text.strip().lower() in EXIT_WORDS


@router.message(F.text == "🔧 Своя сборка")
async def start_build(message: types.Message, state: FSMContext):
    await message.answer("Введите конфигурацию, например <b>14S12P</b>:", parse_mode="HTML")
    await state.set_state(BuildState.waiting_for_config)


@router.message(BuildState.waiting_for_config, F.text)
async def process_build(message: types.Message, state: FSMContext):
    if _is_exit(message.text):
        # кнопка меню или «отмена» во время ввода — чистый выход из сценария
        # (раньше текст кнопки падал в «Неверный формат» и сценарий «залипал»)
        await state.clear()
        await message.answer("❌ Своя сборка закрыта. Выберите раздел в меню.")
        return
    match = re.match(r"^(\d+)[sS](\d+)[pP]$", message.text.strip())
    if not match:
        await message.answer("Неверный формат. Пример: <b>14S12P</b>", parse_mode="HTML")
        return

    s, p = int(match.group(1)), int(match.group(2))
    nom_v = s * 3.7
    max_v = s * 4.2
    work_min_v = s * 3.0
    abs_min_v = s * 2.5
    cap_mah = p * 4000
    cap_ah = p * 4
    energy_wh = nom_v * cap_ah
    weight_kg = s * p * 0.072 + 0.040
    cont_a = 35 * p
    peak_a = 45 * p
    charge_a = 2 * p
    total_cells = s * p
    balance_pins = s + 1

    await state.update_data(
        s=s, p=p, nom_v=nom_v, max_v=max_v, work_min_v=work_min_v, abs_min_v=abs_min_v,
        cap_mah=cap_mah, cap_ah=cap_ah, energy_wh=energy_wh, weight_kg=weight_kg,
        cont_a=cont_a, peak_a=peak_a, charge_a=charge_a,
        total_cells=total_cells, balance_pins=balance_pins,
    )
    await state.set_state(BuildState.waiting_for_connector)

    if s <= 6:
        await state.update_data(connector="XT60")
        await message.answer(
            f"Расчёт для {s}S{p}P готов.\n\nРазъём по умолчанию: <b>XT60</b>.\n"
            "Отправьте «ок» — оставить XT60, или напишите другой тип разъёма:",
            parse_mode="HTML",
        )
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="XT90-S", callback_data="build_conn:XT90S"),
            InlineKeyboardButton(text="XT60", callback_data="build_conn:XT60"),
            InlineKeyboardButton(text="Другой", callback_data="build_conn:other"),
        ]])
        await message.answer(f"Выберите разъём для {s}S{p}P:", reply_markup=kb)


@router.message(BuildState.waiting_for_config)
async def process_build_not_text(message: types.Message):
    # не-текст (фото, голос и т.п.) во время ввода — подсказка (как в calculator/faq);
    # раньше такой апдейт ронял обработчик на message.text.strip()
    await message.answer("Отправьте конфигурацию текстом, например <b>14S12P</b>.", parse_mode="HTML")


async def _send_build_result(target, data: dict, connector: str):
    text = f"""
<b>Расчёт для {data['s']}S{data['p']}P:</b>

Ячеек всего: {data['total_cells']} шт. (21700)

Ёмкость: {data['cap_mah']} мА·ч ({data['cap_ah']} А·ч)
Энергия: ~{data['energy_wh']:.0f} Вт·ч
Масса сборки: ~{data['weight_kg']:.2f} кг

Напряжение номинал: {data['nom_v']:.1f} В
Заряд (макс): {data['max_v']:.1f} В
Разряд (рабочий): не ниже {data['work_min_v']:.1f} В
Абсолютный минимум: {data['abs_min_v']:.1f} В

Ток длительно: {data['cont_a']} А
Ток пиково: {data['peak_a']} А
Ток заряда (0,5C): {data['charge_a']} А

Разъём: {connector}
Балансир: {data['balance_pins']}-pin
"""
    if isinstance(target, types.Message) and target.from_user and target.from_user.id == bot.id:
        try:
            await target.edit_text(text, parse_mode="HTML", reply_markup=None)
            return
        except Exception as e:
            system_logger.warning(f"Build result edit failed: {e}")
    await target.answer(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("build_conn:"))
async def callback_build_connector(callback: types.CallbackQuery, state: FSMContext):
    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()
    if "s" not in data or "p" not in data:
        await state.clear()
        await callback.message.answer(
            "Сессия расчёта истекла. Нажмите «🔧 Своя сборка» и введите конфигурацию заново."
        )
        await callback.answer()
        return

    if choice == "other":
        await callback.message.answer("Напишите тип разъёма:")
        await callback.answer()
        return

    connector = "XT90-S" if choice == "XT90S" else "XT60"
    await _send_build_result(callback.message, data, connector)
    await state.clear()
    await callback.answer()


@router.message(BuildState.waiting_for_connector, F.text)
async def process_build_connector(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if "s" not in data or "p" not in data:
        await state.clear()
        await message.answer(
            "Сессия расчёта истекла. Нажмите «🔧 Своя сборка» и введите конфигурацию заново."
        )
        return

    text = (message.text or "").strip()
    if _is_exit(text):
        # кнопка меню или «отмена» — выход; раньше текст кнопки становился
        # «типом разъёма» и подставлялся в результат расчёта
        await state.clear()
        await message.answer("❌ Своя сборка закрыта. Выберите раздел в меню.")
        return
    if not text:
        await message.answer("Напишите тип разъёма текстом.")
        return

    if text.lower() in {"ок", "ok", "-", "✅"}:
        connector = data.get("connector") or "XT60"
    else:
        connector = text

    await _send_build_result(message, data, connector)
    await state.clear()


@router.message(BuildState.waiting_for_connector)
async def process_build_connector_not_text(message: types.Message):
    await message.answer("Напишите тип разъёма текстом.")