import json
from datetime import datetime

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import dialog_logger, system_logger
from data import (
    MODELS, PLATFORMS, FILTER_LABELS, SELECTION_STEP_C,
    SELECTION_6S_CONFLICT, SELECTION_6S_CONFLICT_LABEL,
    SELECTION_DRONE_BY_VOLTAGE, SEL_STEP_A_TEXT,
)
from keyboards import SEARCH_HEADER_TEXT, _build_search_kb
from states import SelectionState
from utils import _apply_filters

router = Router()


def _sel_kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows
    ])


def _sel_step_a_kb():
    return _sel_kb([[("6S", "sel:a:6S"), ("8S", "sel:a:8S")],
                    [("Не знаю", "sel:a:unknown")]])


def _sel_step_b_kb():
    return _sel_kb([
        [("🚀 Рывок (перехват, ПВО, гонки)", "sel:b:rush")],
        [("🛣 Километры (разведка, катушка, доставка)", "sel:b:km")],
    ])


def _sel_drone_codes():
    return [d for d in PLATFORMS if any(d in m["drones"] for m in MODELS.values())]


def _sel_drone_frames(drone):
    return sorted({m["voltage"] for m in MODELS.values() if drone in m["drones"]})


def _sel_step_drone_kb():
    rows = [[(FILTER_LABELS["drone"][d], f"sel:dr:{d}")] for d in _sel_drone_codes()]
    rows.append([("⬅️ Начать заново", "sel:restart")])
    return _sel_kb(rows)


def _sel_step_frame_kb(drone):
    rows = [[(FILTER_LABELS["voltage"][f], f"sel:fr:{f}")] for f in _sel_drone_frames(drone)]
    rows.append([("⬅️ Выбрать другой дрон", "sel:drback")])
    rows.append([("⬅️ Начать заново", "sel:restart")])
    return _sel_kb(rows)


def _sel_unk_none_kb():
    return _sel_kb([[("⬅️ Выбрать другой дрон", "sel:drback")],
                    [("⬅️ Начать заново", "sel:restart")]])


def _sel_log(callback, step, choice):
    dialog_logger.info(json.dumps({
        'event': 'selection_step',
        'user_id': callback.from_user.id,
        'step': step,
        'choice': choice,
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))


async def _sel_show_step_b(callback, voltage):
    try:
        await callback.message.edit_text(
            f"🧠 Экспресс-подбор АКБ\n\nНапряжение: {FILTER_LABELS['voltage'][voltage]}\n\n"
            "Вопрос 2/3: Рывок или километры?",
            reply_markup=_sel_step_b_kb(),
        )
    except Exception as e:
        system_logger.warning(f"Selection step_b edit failed: {e}")


def _sel_step_c_kb(voltage):
    rows = [[(label, f"sel:c:{key}")] for key, (label, _c) in SELECTION_STEP_C[voltage].items()]
    if voltage == "6S":
        rows.append([(SELECTION_6S_CONFLICT_LABEL, f"sel:c:{SELECTION_6S_CONFLICT}")])
    return _sel_kb(rows)


async def _sel_show_step_c(callback, voltage):
    try:
        await callback.message.edit_text(
            f"🧠 Экспресс-подбор АКБ\n\nНапряжение: {FILTER_LABELS['voltage'][voltage]}\n\n"
            "Вопрос 3/3: Что на борту кроме батареи?",
            reply_markup=_sel_step_c_kb(voltage),
        )
    except Exception as e:
        system_logger.warning(f"Selection step_c edit failed: {e}")


async def _sel_show_step_drone(callback, state):
    await state.set_state(SelectionState.step_drone)
    try:
        await callback.message.edit_text(
            "🧠 Экспресс-подбор АКБ — «Не знаю»\n\nПодберу АКБ по дрону. Выберите дрон:",
            reply_markup=_sel_step_drone_kb(),
        )
    except Exception as e:
        system_logger.warning(f"Selection step_drone edit failed: {e}")


async def _sel_show_step_frame(callback, state, drone):
    await state.set_state(SelectionState.step_frame)
    frames = _sel_drone_frames(drone)
    try:
        await callback.message.edit_text(
            "🧠 Экспресс-подбор АКБ — «Не знаю»\n\n"
            f"Дрон: {FILTER_LABELS['drone'][drone]}\nПод этот дрон подходят рамы:\n"
            + "".join(f"• {FILTER_LABELS['voltage'][f]}\n" for f in frames)
            + "\nВыберите раму:",
            reply_markup=_sel_step_frame_kb(drone),
        )
    except Exception as e:
        system_logger.warning(f"Selection step_frame edit failed: {e}")


async def _sel_show_unk_results(callback, state, drone, frame):
    models = _apply_filters(drone=drone, voltage=frame)
    frames = _sel_drone_frames(drone)
    buttons = [[InlineKeyboardButton(
        text=f"{code} | {m['mah']}мАч | {m['kg']}кг",
        callback_data=f"model:{code}",
    )] for code, m in models.items()]
    if len(frames) > 1:
        buttons.append([InlineKeyboardButton(text="⬅️ Вернуться к выбору рамы", callback_data="sel:frback")])
    buttons.append([InlineKeyboardButton(text="⬅️ Выбрать другой дрон", callback_data="sel:drback")])
    buttons.append([InlineKeyboardButton(text="⬅️ Начать заново", callback_data="sel:restart")])

    text = (f"🧠 Экспресс-подбор АКБ — кандидаты\n\n"
            f"Дрон: {FILTER_LABELS['drone'][drone]}\nРама: {FILTER_LABELS['voltage'][frame]}\n\n"
            f"Подходящих моделей: {len(models)}\n\nВыберите модель:")
    if len(models) == 1 and len(frames) > 1:
        only = next(iter(models))
        text += (f"\n\nПод эту раму подходит одна модель — {only}. "
                 "Если она не подходит, вернитесь к выбору рамы.")

    await state.update_data(drone=drone, frame=frame)
    await state.set_state(SelectionState.step_frame)
    try:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception as e:
        system_logger.warning(f"Selection unk results edit failed: {e}")


async def _sel_show_results(callback, state, voltage, choice):
    label, codes = SELECTION_STEP_C[voltage][choice]
    drone = SELECTION_DRONE_BY_VOLTAGE[voltage]
    buttons = [[InlineKeyboardButton(
        text=f"{code} | {MODELS[code]['mah']}мАч | {MODELS[code]['kg']}кг",
        callback_data=f"model:{code}",
    )] for code in codes]
    buttons.append([InlineKeyboardButton(text="🔎 Уточнить через Поиск",
                                         callback_data=f"sel:search:v={voltage}:d={drone}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Начать заново", callback_data="sel:restart")])
    text = (f"🧠 Экспресс-подбор — кандидаты\n\n"
            f"Напряжение: {FILTER_LABELS['voltage'][voltage]}\nЗадача: {label}\n\n"
            f"Подходящих моделей: {len(codes)}\n\n"
            "Это предварительный подбор. Для точного расчёта дальности и проверки тока "
            "используйте 🔎 Поиск или напишите мне задачу.")
    try:
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception as e:
        system_logger.warning(f"Selection results edit failed: {e}")
    await state.clear()


@router.message(F.text == "🧠 Подбор")
async def button_selection(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(SelectionState.step_a)
    await message.answer(SEL_STEP_A_TEXT, reply_markup=_sel_step_a_kb())


@router.callback_query(F.data.startswith("sel:"))
async def callback_selection(callback: types.CallbackQuery, state: FSMContext):
    payload = callback.data[len("sel:"):]
    action, _, arg = payload.partition(":")
    current = await state.get_state()

    if action == "restart":
        await state.clear()
        await state.set_state(SelectionState.step_a)
        _sel_log(callback, "restart", "restart")
        try:
            await callback.message.edit_text(SEL_STEP_A_TEXT, reply_markup=_sel_step_a_kb())
        except Exception as e:
            system_logger.warning(f"Selection restart failed: {e}")

    elif action == "a" and current == SelectionState.step_a:
        _sel_log(callback, "step_a", arg)
        if arg == "unknown":
            await _sel_show_step_drone(callback, state)
        elif arg in ("6S", "8S"):
            await state.update_data(voltage=arg)
            await state.set_state(SelectionState.step_b)
            await _sel_show_step_b(callback, arg)

    elif action == "b" and current == SelectionState.step_b:
        _sel_log(callback, "step_b", arg)
        if arg == "rush":
            await state.clear()
            dialog_logger.info(json.dumps({
                'event': 'selection_rejected',
                'user_id': callback.from_user.id,
                'reason': 'li_po_needed',
                'timestamp': datetime.now().isoformat()
            }, ensure_ascii=False))
            try:
                await callback.message.edit_text(
                    "Под перехват, ПВО и гонки нужен LiPo — это не наш продукт.\n"
                    "Наша батарея Li-Ion создана для дальности. Если задача изменится — пишите, подберём.",
                    reply_markup=_sel_kb([[("⬅️ Начать заново", "sel:restart")]]),
                )
            except Exception as e:
                system_logger.warning(f"Selection reject edit failed: {e}")
        elif arg == "km":
            data = await state.get_data()
            voltage = data.get("voltage", "6S")
            await state.set_state(SelectionState.step_c)
            await _sel_show_step_c(callback, voltage)

    elif action == "dr" and current == SelectionState.step_drone:
        drone = arg
        _sel_log(callback, "step_drone", drone)
        frames = _sel_drone_frames(drone)
        if not frames:
            await state.clear()
            try:
                await callback.message.edit_text(
                    f"🧠 Экспресс-подбор АКБ\n\nДля дрона {FILTER_LABELS['drone'][drone]} "
                    "не нашлось моделей в каталоге. Выберите другой дрон или начните заново.",
                    reply_markup=_sel_unk_none_kb(),
                )
            except Exception as e:
                system_logger.warning(f"Selection drone-empty edit failed: {e}")
        elif len(frames) == 1:
            await _sel_show_unk_results(callback, state, drone, frames[0])
        else:
            await _sel_show_step_frame(callback, state, drone)

    elif action == "fr" and current == SelectionState.step_frame:
        frame = arg
        data = await state.get_data()
        drone = data.get("drone")
        if not drone:
            await _sel_show_step_drone(callback, state)
        else:
            _sel_log(callback, "step_frame", f"{drone}:{frame}")
            if not _apply_filters(drone=drone, voltage=frame):
                await state.clear()
                try:
                    await callback.message.edit_text(
                        f"🧠 Экспресс-подбор АКБ\n\nДля дрона {FILTER_LABELS['drone'][drone]} на раме "
                        f"{FILTER_LABELS['voltage'][frame]} не нашлось подходящих моделей. "
                        "Выберите другой дрон или начните заново.",
                        reply_markup=_sel_unk_none_kb(),
                    )
                except Exception as e:
                    system_logger.warning(f"Selection frame-empty edit failed: {e}")
            else:
                await _sel_show_unk_results(callback, state, drone, frame)

    elif action == "drback":
        _sel_log(callback, "drback", "drback")
        await _sel_show_step_drone(callback, state)

    elif action == "frback" and current == SelectionState.step_frame:
        data = await state.get_data()
        drone = data.get("drone")
        if not drone:
            await _sel_show_step_drone(callback, state)
        else:
            _sel_log(callback, "frback", drone)
            await _sel_show_step_frame(callback, state, drone)

    elif action == "c" and current == SelectionState.step_c and arg == SELECTION_6S_CONFLICT:
        _sel_log(callback, "step_c", "6S:cl")
        try:
            await callback.message.edit_text(
                'На 10" с катушкой 20 км и грузом задача не решается. Нужен переход на 13" 8S.',
                reply_markup=_sel_kb([[("Перейти к подбору 8S", "sel:to8s")],
                                      [("⬅️ Начать заново", "sel:restart")]]),
            )
        except Exception as e:
            system_logger.warning(f"Selection conflict edit failed: {e}")

    elif action == "c" and current == SelectionState.step_c:
        data = await state.get_data()
        voltage = data.get("voltage", "8S")
        if arg in SELECTION_STEP_C[voltage]:
            _sel_log(callback, "step_c", f"{voltage}:{arg}")
            await _sel_show_results(callback, state, voltage, arg)

    elif action == "to8s" and current == SelectionState.step_c:
        await state.update_data(voltage="8S")
        await state.set_state(SelectionState.step_c)
        _sel_log(callback, "to8s", "8S")
        await _sel_show_step_c(callback, "8S")

    elif action == "search":
        params = dict(p.split("=", 1) for p in arg.split(":") if "=" in p)
        voltage = params.get("v", "all")
        drone = params.get("d", "all")
        await state.clear()
        _sel_log(callback, "search", f"v={voltage}:d={drone}")
        try:
            await callback.message.answer(
                SEARCH_HEADER_TEXT,
                reply_markup=_build_search_kb(drone=drone, voltage=voltage),
                parse_mode="HTML",
            )
        except Exception as e:
            system_logger.warning(f"Selection to-search failed: {e}")

    await callback.answer()