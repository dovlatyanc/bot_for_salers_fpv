"""
Модуль «🔧 Своя сборка» (кнопка главного меню): расчёт нестандартной АКБ
из ячеек 21700 под запрос менеджера.

Ввод конфигурации (FSM BuildState):
  1) готовая геометрия — «14S12P» (латиница или «14с12р»);
  2) параметры сборки, которые бот переводит в S/P сам:
       • «12В 12000мА·ч» — напряжение (→ S, номинал ячейки 3,7 В) и ёмкость
         (→ P, ячейка 4000 мА·ч) одним сообщением;
       • «8S» / «12В» / «12000мА·ч» / «90А» (ток → P, 35 А длительно на
         параллельную группу) — по одному параметру, недостающее бот
         доспрашивает в том же шаге;
  3) выбор разъёма (XT60 по умолчанию для 6S и ниже, инлайн-меню для 7S+)
     и итоговая карточка: ёмкость, энергия, масса, напряжения, токи, балансир.

Модуль не импортирует bot.py (см. handlers/__init__.py); логи — через
logging.getLogger(...) (настраиваются в logging_setup.py).
"""

import math
import re

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import bot, system_logger
from keyboards import MENU_EXIT_BUTTONS
from states import BuildState
from utils.ui import safe_answer

router = Router()

BUILD_BUTTON = "🔧 Своя сборка"
# Кнопки меню (+ легаси «📊 ТТХ»), по которым можно выйти из ввода (сама
# «🔧 Своя сборка» — перезапуск: её перехватывает start_build, зарегистрированный
# раньше FSM). Зеркало MENU_EXIT_BUTTONS — приём calculator/faq/followup.
MENU_BUTTONS = frozenset(MENU_EXIT_BUTTONS) - {BUILD_BUTTON}
EXIT_WORDS = frozenset({"отмена", "cancel", "❌", "❌ отмена"})

# Стандарт ячейки 21700 — все расчёты сборки и переводы параметров в S/P
CELL_NOM_V = 3.7       # номинальное напряжение ячейки, В
CELL_MAX_V = 4.2       # напряжение полного заряда, В
CELL_CAP_MAH = 4000    # ёмкость ячейки, мА·ч
CELL_CONT_A = 35       # длительный ток на параллельную группу, А
CELL_PEAK_A = 45       # пиковый ток на параллельную группу, А
CELL_CHARGE_A = 2      # ток заряда 0,5C на группу, А
CELL_WEIGHT_KG = 0.072
PACK_WEIGHT_KG = 0.040
MAX_S, MAX_P = 16, 20          # разумные пределы «самосбора»
MIN_REQ_V, MAX_REQ_V = 2.0, 60.0  # входное напряжение запроса, В

# --- распознавание ввода конфигурации --------------------------------------
# латиница и кириллица: s/с, p/р; десятичные «12,5» и «12.5»;
# ёмкость: «12000мА·ч», «12000 mah», «12ач», «12ah»; ток: «90А», «12 амп»
_RE_CONFIG = re.compile(r"(\d+)\s*[sс]\s*(\d+)\s*[pр]")
_RE_S_ONLY = re.compile(r"(\d+)\s*[sс](?![а-яa-z0-9])")
_RE_P_ONLY = re.compile(r"(\d+)\s*[pр](?![а-яa-z0-9])")
_RE_VOLT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:вольт(?:а|ов)?|в|v)(?![а-яa-z])")
_RE_AMPS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:ампер(?:а|ов)?|амп|а|a)(?![чh])")
_RE_CAP = re.compile(r"(\d+)\s*(?:м\s*а\s*ч|а\s*ч|m\s*a\s*h|a\s*h)")


def _to_num(raw: str) -> float:
    return float(raw.replace(",", "."))


def _fmt_num(x: float) -> str:
    return f"{x:g}"


def _parse_build_input(text: str) -> dict | None:
    """Разбор запроса менеджера → {s, p, v, cap_mah, amps} (None = не задано).

    Понимает: «14S12P»/«14с12р», одиночные «8S»/«5P», напряжение «12В»,
    ёмкость «12000мА·ч» (в т.ч. «12ач»/«12ah» → мА·ч) и ток «90А» —
    по отдельности и в комбинациях («12В 12000мА·ч 90А»).
    Возвращает None, если узнаваемых параметров нет вообще — шаг покажет
    подсказку с форматами.
    """
    t = re.sub(r"\s+", " ", (text or "").strip().lower().replace("·", ""))
    if not t:
        return None

    m = _RE_CONFIG.search(t)
    if m:  # полная геометрия — остальные параметры не важны
        return {"s": int(m.group(1)), "p": int(m.group(2)),
                "v": None, "cap_mah": None, "amps": None}

    m = _RE_S_ONLY.search(t)
    s = int(m.group(1)) if m else None
    m = _RE_P_ONLY.search(t)
    p = int(m.group(1)) if m else None
    m = _RE_VOLT.search(t)
    v = _to_num(m.group(1)) if m else None

    m = _RE_CAP.search(t)
    cap_mah = None
    if m:
        unit = t[m.start(1) + len(m.group(1)):m.end()].strip()
        value = int(m.group(1))
        # «мач/mah» — уже мА·ч; «ач/ah» — ампер-часы → ×1000
        cap_mah = value if unit[:1] in ("м", "m") else value * 1000

    m = _RE_AMPS.search(t)
    amps = _to_num(m.group(1)) if m else None

    if s is None and p is None and v is None and cap_mah is None and amps is None:
        return None
    return {"s": s, "p": p, "v": v, "cap_mah": cap_mah, "amps": amps}


def _is_exit(text: str) -> bool:
    # это выход, если текст — кнопка меню (кроме самой «🔧 Своя сборка») или слово отмены
    return text in MENU_BUTTONS or text.strip().lower() in EXIT_WORDS


@router.message(F.text == "🔧 Своя сборка")
async def start_build(message: types.Message, state: FSMContext):
    await message.answer(
        "Введите конфигурацию, например <b>14S12P</b>.\n\n"
        "Также понимаю параметры: <b>12В 12000мА·ч</b> (напряжение и ёмкость), "
        "<b>8S</b>, <b>12000мА·ч</b> или <b>90А</b> — по одному параметру, "
        "недостающее уточню вопросом.",
        parse_mode="HTML",
    )
    await state.set_state(BuildState.waiting_for_config)


_HINT_TEXT = (
    "Не понял запрос. Форматы, которые понимаю:\n"
    "• <b>14S12P</b> — готовая геометрия (можно «14с12р»)\n"
    "• <b>12В 12000мА·ч</b> — напряжение и ёмкость сразу\n"
    "• <b>8S</b>, <b>12000мА·ч</b> или <b>90А</b> — по одному параметру, "
    "остальное уточню"
)


@router.message(BuildState.waiting_for_config, F.text)
async def process_build(message: types.Message, state: FSMContext):
    if _is_exit(message.text):
        # кнопка меню или «отмена» во время ввода — чистый выход из сценария
        # (раньше текст кнопки падал в «Неверный формат» и сценарий «залипал»)
        await state.clear()
        await message.answer("❌ Своя сборка закрыта. Выберите раздел в меню.")
        return

    parsed = _parse_build_input(message.text)
    if parsed is None:
        await message.answer(_HINT_TEXT, parse_mode="HTML")
        return

    data = await state.get_data()
    v, cap, amps = parsed["v"], parsed["cap_mah"], parsed["amps"]
    notes: list[str] = []   # «как понял запрос» — показываем менеджеру

    # S: явная геометрия → напряжение запроса → параметр прошлого сообщения
    s = parsed["s"]
    s_note = ""
    if s is None and v is not None:
        if not (MIN_REQ_V <= v <= MAX_REQ_V):
            await message.answer(
                "Напряжение должно быть 2–60 В. Например: <b>12В</b>, "
                "<b>22,2В</b> или <b>29.6V</b>.",
                parse_mode="HTML",
            )
            return
        s = int(round(v / CELL_NOM_V))
        s_note = f"{_fmt_num(v)} В ≈ {s}S (номинал {s * CELL_NOM_V:.1f} В)"
        notes.append(s_note)
    elif s is None and data.get("pending_s"):
        s = data["pending_s"]
        if data.get("pending_s_note"):
            notes.append(data["pending_s_note"])

    # P: явная геометрия → ёмкость/ток запроса (берём большее) → прошлое сообщение
    p = parsed["p"]
    p_notes: list[str] = []
    if p is None and (cap or amps):
        variants = []
        if cap:
            pc = math.ceil(cap / CELL_CAP_MAH)
            variants.append(pc)
            p_notes.append(f"{cap} мА·ч ≈ {pc}P (ячейка {CELL_CAP_MAH} мА·ч)")
        if amps:
            pa = math.ceil(amps / CELL_CONT_A)
            variants.append(pa)
            p_notes.append(
                f"{_fmt_num(amps)} А ≈ {pa}P (длительно {CELL_CONT_A} А на группу)"
            )
        p = max(variants)
        notes.extend(p_notes)
    elif p is None and data.get("pending_p"):
        p = data["pending_p"]
        if data.get("pending_p_note"):
            notes.append(data["pending_p_note"])

    if s is None or p is None:
        # частичный ввод — запоминаем понятую часть и доспрашиваем остальное
        upd: dict = {}
        if s is not None:
            upd["pending_s"] = s
            upd["pending_s_note"] = s_note or (data.get("pending_s_note") or "")
        if p is not None:
            upd["pending_p"] = p
            upd["pending_p_note"] = "; ".join(p_notes) or (data.get("pending_p_note") or "")
        await state.update_data(**upd)
        if p is None:
            head = f"Понял: {notes[0]}." if notes else f"Понял: {s}S."
            await message.answer(
                f"{head}\nТеперь укажите ёмкость или ток: например "
                "<b>12000мА·ч</b> или <b>90А</b>.",
                parse_mode="HTML",
            )
        else:
            head = f"Понял: {notes[0]}." if notes else f"Понял: {p}P."
            await message.answer(
                f"{head}\nТеперь укажите напряжение: например <b>12В</b>, "
                "<b>22,2В</b> или просто <b>8S</b>.",
                parse_mode="HTML",
            )
        return

    if not (1 <= s <= MAX_S and 1 <= p <= MAX_P):
        await message.answer(
            f"{s}S{p}P — не похоже на реальную сборку. Диапазон: 1–{MAX_S}S "
            f"и 1–{MAX_P}P. Проверьте параметры и напишите ещё раз.",
            parse_mode="HTML",
        )
        return

    nom_v = s * CELL_NOM_V
    max_v = s * CELL_MAX_V
    work_min_v = s * 3.0
    abs_min_v = s * 2.5
    cap_mah = p * CELL_CAP_MAH
    cap_ah = cap_mah / 1000
    energy_wh = nom_v * cap_ah
    weight_kg = s * p * CELL_WEIGHT_KG + PACK_WEIGHT_KG
    cont_a = CELL_CONT_A * p
    peak_a = CELL_PEAK_A * p
    charge_a = CELL_CHARGE_A * p
    total_cells = s * p
    balance_pins = s + 1

    await state.update_data(
        s=s, p=p, nom_v=nom_v, max_v=max_v, work_min_v=work_min_v, abs_min_v=abs_min_v,
        cap_mah=cap_mah, cap_ah=cap_ah, energy_wh=energy_wh, weight_kg=weight_kg,
        cont_a=cont_a, peak_a=peak_a, charge_a=charge_a,
        total_cells=total_cells, balance_pins=balance_pins,
        pending_s=None, pending_p=None, pending_s_note=None, pending_p_note=None,
    )
    await state.set_state(BuildState.waiting_for_connector)

    recap = "Как понял запрос: " + "; ".join(notes) + ".\n\n" if notes else ""
    if s <= 6:
        await state.update_data(connector="XT60")
        await message.answer(
            f"{recap}Расчёт для {s}S{p}P готов.\n\nРазъём по умолчанию: <b>XT60</b>.\n"
            "Отправьте «ок» — оставить XT60, или напишите другой тип разъёма:",
            parse_mode="HTML",
        )
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="XT90-S", callback_data="build_conn:XT90S"),
            InlineKeyboardButton(text="XT60", callback_data="build_conn:XT60"),
            InlineKeyboardButton(text="Другой", callback_data="build_conn:other"),
        ]])
        await message.answer(f"{recap}Выберите разъём для {s}S{p}P:", reply_markup=kb)


@router.message(BuildState.waiting_for_config)
async def process_build_not_text(message: types.Message):
    # не-текст (фото, голос и т.п.) во время ввода — подсказка (как в calculator/faq);
    # раньше такой апдейт ронял обработчик на message.text.strip()
    await message.answer(
        "Отправьте конфигурацию текстом: например <b>14S12P</b> "
        "или <b>12В 12000мА·ч</b>.",
        parse_mode="HTML",
    )


async def _send_build_result(target, data: dict, connector: str):
    text = f"""
<b>Расчёт для {data['s']}S{data['p']}P:</b>

Ячеек всего: {data['total_cells']} шт. (21700)

Ёмкость: {data['cap_mah']} мА·ч ({data['cap_ah']:g} А·ч)
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
        await safe_answer(callback, )
        return

    if choice == "other":
        await callback.message.answer("Напишите тип разъёма:")
        await safe_answer(callback, )
        return

    connector = "XT90-S" if choice == "XT90S" else "XT60"
    await _send_build_result(callback.message, data, connector)
    await state.clear()
    await safe_answer(callback, )


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