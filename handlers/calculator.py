"""
Модуль «💰 Калькулятор маржи» (кнопки «💰 Калькулятор» и «📊 История расчётов»
в главном меню бота).

Сценарий (FSM MarginState):
  1) менеджер выбирает модель АКБ из списка (список — ключи config/prices.json);
  2) бот показывает полный прайс модели (закупка, розница, опт, НДС 5/22%) и
     предлагает закупочную цену из конфига одной кнопкой — либо свою;
  3) вводит цену продажи клиенту;
  4) бот показывает маржу в рублях, маржу в %, рекомендацию по скидке и сверку
     с прайсом (факт vs плановая маржа; между какими ступенями прайса цена).

Каждый завершённый расчёт сохраняется в config/calc_history.json —
кнопка «📊 История расчётов» показывает последние 5 расчётов этого менеджера.

Модуль не импортирует bot.py (см. handlers/__init__.py): список моделей и базовые
цены берутся из config/prices.json, логи пишутся через logging.getLogger(...)
(логгеры 'dialog' и 'system' настраиваются в logging_setup.py).

Подключение (в bot.py, сразу после создания Dispatcher):
    from handlers.calculator import router as calculator_router
    dp.include_router(calculator_router)
"""

import asyncio
import html
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from aiogram import F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from keyboards import MAIN_BUTTONS

router = Router(name="calculator")

# Общие логгеры бота (настраиваются в bot.py) — свои хендлеры к ним не добавляем
dialog_logger = logging.getLogger("dialog")
system_logger = logging.getLogger("system")

# Пути к конфигам — от каталога проекта (родитель пакета handlers), независимо от CWD
BASE_DIR = Path(__file__).resolve().parent.parent
PRICES_PATH = BASE_DIR / "config" / "prices.json"
HISTORY_PATH = BASE_DIR / "config" / "calc_history.json"

HISTORY_LIMIT = 5      # «последние 5 расчётов этого менеджера»
DISCOUNT_SHARE = 0.30  # рекомендованный предел скидки — 30% от маржи

CALC_BUTTON = "💰 Калькулятор"
HISTORY_BUTTON = "📊 История расчётов"

# Кнопки главного меню — зеркало MAIN_BUTTONS из keyboards (без кнопок самого
# калькулятора) — по ним можно выйти из FSM-сценария, не оставляя «зависшее»
# состояние. Выводим из MAIN_BUTTONS (как в faq/followup/build), чтобы зеркало
# не рассинхронизировалось при добавлении новых кнопок меню.
MENU_BUTTONS = frozenset(MAIN_BUTTONS) - {CALC_BUTTON, HISTORY_BUTTON}
# слова-выходы, принимаемые в шагах ввода цен (кроме /cancel — он ловится командой)
EXIT_WORDS = frozenset({"отмена", "cancel", "❌", "❌ отмена"})


# ============================================================
# FSM-СОСТОЯНИЯ
# ============================================================
class MarginState(StatesGroup):
    choosing_model = State()      # шаг 1: выбор модели
    waiting_for_cost = State()    # шаг 2: закупочная цена
    waiting_for_price = State()   # шаг 3: цена продажи клиенту


# ============================================================
# ЧИСТАЯ ЛОГИКА: ЦЕНЫ, ПАРСИНГ, ФОРМАТИРОВАНИЕ
# ============================================================
def _load_prices() -> dict:
    """Прайс-лист из config/prices.json: {модель: {cost, cash, wholesale, vat5, vat22, profit}}.

    Обязательное поле — cost (закупочная цена > 0); остальные ступени необязательны.
    Поддерживается и старый плоский формат {модель: закупка} — он превращается
    в {модель: {"cost": закупка}}. Битые записи пропускаются (причина — в system-лог).
    Пустой dict — файл отсутствует/битый (причина пишется в system-лог).
    """
    try:
        with open(PRICES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        system_logger.warning(f"prices.json not found: {PRICES_PATH}")
        return {}
    except Exception as e:
        system_logger.warning(f"prices.json read failed: {e}")
        return {}
    if not isinstance(data, dict):
        system_logger.warning("prices.json: ожидается JSON-объект {модель: {cost, cash, ...}}")
        return {}
    clean: dict = {}
    for code, value in data.items():
        if isinstance(value, (int, float)):        # старый формат: число = закупка
            value = {"cost": value}
        if not isinstance(value, dict):
            system_logger.warning(f"prices.json: некорректная запись для {code}: {value!r}")
            continue
        entry: dict = {}
        for key in ("cost", "cash", "wholesale", "vat5", "vat22", "profit"):
            if key not in value:
                continue
            try:
                price = float(value[key])
            except (TypeError, ValueError):
                system_logger.warning(f"prices.json: некорректная цена {key} для {code}: {value[key]!r}")
                continue
            if price > 0:
                entry[key] = price
        if entry.get("cost", 0) <= 0:
            system_logger.warning(f"prices.json: нет закупочной цены для {code} — запись пропущена")
            continue
        clean[str(code)] = entry
    return clean


def _model_sort_key(code: str) -> tuple:
    # NEW: сортировка моделей «6S2P … 6S6P, 8S2P … 8S8P», нестандартные коды — в конец
    m = re.match(r"^(\d+)[sS](\d+)[pP]", code)
    if m:
        return (int(m.group(1)), int(m.group(2)), 0, code)
    return (999, 999, 1, code)


def _parse_money(text: str | None) -> float | None:
    """Парсит сумму: '15000', '15 000', '28,5', '28к', '15 000 руб.' → float.

    None — если это не положительное число. Запятая и точка — десятичный
    разделитель, суффикс 'к'/'k' — тысячи.
    """
    if not text:
        return None
    s = text.strip().lower().replace("\u00a0", " ")
    s = re.sub(r"(руб\.?|₽|р\.?)", "", s)  # валютные суффиксы
    s = s.replace(" ", "")
    mult = 1.0
    if s.endswith(("к", "k")):
        mult = 1000.0
        s = s[:-1]
    if s.count(",") and s.count("."):
        s = s.replace(",", "")  # '15,000.50' → запятые-разделители тысяч убираем
    else:
        s = s.replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", s):
        return None
    try:
        value = float(s) * mult
    except ValueError:
        return None
    return value if value > 0 else None


def _fmt_money(value: float) -> str:
    # NEW: 15000 → '15 000 ₽'; 1500.5 → '1 500.5 ₽'
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", " ") + " ₽"


def _fmt_pct(value: float) -> str:
    return f"{value:.1f} %"


# Ступени прайса в порядке показа: ключ в prices.json → подпись
PRICE_FIELDS: tuple[tuple[str, str], ...] = (
    ("cost", "закупка"),
    ("cash", "розница"),
    ("wholesale", "опт"),
    ("vat5", "НДС 5%"),
    ("vat22", "НДС 22%"),
)


def _price_ladder(info: dict) -> str:
    """Строка «закупка … · розница … · опт … · НДС 5% … · НДС 22% …» (только заполненные)."""
    parts = [f"{title} {_fmt_money(info[key])}" for key, title in PRICE_FIELDS if info.get(key)]
    return " · ".join(parts)


def _price_review(price: float, margin: float, info: dict | None) -> str:
    """Блок «Сверка с прайсом» для экрана результата.

    Показывает плановую маржу из прайса (поле profit) и положение цены продажи
    между ступенями прайса. Пустая строка — прайса для модели нет.
    """
    if not info:
        return ""
    lines: list[str] = ["", "🏷 <b>Сверка с прайсом:</b>"]
    plan = info.get("profit")
    if plan:
        delta = margin - float(plan)
        if abs(delta) < 0.5:
            verdict = "на уровне плана"
        elif delta > 0:
            verdict = f"выше плана на {_fmt_money(delta)}"
        else:
            verdict = f"ниже плана на {_fmt_money(abs(delta))}"
        lines.append(f"💰 План по прайсу: маржа {_fmt_money(float(plan))}. "
                     f"Факт: {_fmt_money(margin)} ({verdict}).")
    tiers = sorted(
        ((title, float(info[key])) for key, title in PRICE_FIELDS if key != "cost" and info.get(key)),
        key=lambda t: t[1],
    )
    if tiers:
        position = ""
        for i, (title, value) in enumerate(tiers):
            if price == value:
                position = f"ровно ступень «{title}» ({_fmt_money(value)})"
                break
            if price < value:
                position = (
                    f"ниже минимальной ступени «{title}» ({_fmt_money(value)})" if i == 0 else
                    f"между «{tiers[i - 1][0]}» ({_fmt_money(tiers[i - 1][1])}) и «{title}» ({_fmt_money(value)})"
                )
                break
        if not position:
            position = f"выше максимальной ступени «{tiers[-1][0]}» ({_fmt_money(tiers[-1][1])})"
        lines.append(f"📍 Ваша цена {_fmt_money(price)} — {position}.")
    return "\n".join(lines)


# ============================================================
# ЧИСТАЯ ЛОГИКА: ИСТОРИЯ РАСЧЁТОВ (config/calc_history.json)
# ============================================================
_history_lock = asyncio.Lock()


def _read_history() -> dict:
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        system_logger.warning(f"calc_history read failed: {e}")
        return {}


def _write_history(data: dict) -> None:
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, HISTORY_PATH)  # атомарная замена
    except Exception as e:
        system_logger.warning(f"calc_history write failed: {e}")


async def add_history_entry(user_id: int, entry: dict) -> None:
    """Сохраняет расчёт менеджера; хранит не более HISTORY_LIMIT последних."""
    async with _history_lock:
        data = _read_history()
        key = str(user_id)
        items = data.get(key, [])
        items.insert(0, entry)  # свежие — в начало
        data[key] = items[:HISTORY_LIMIT]
        _write_history(data)


def get_history(user_id: int) -> list[dict]:
    """Последние расчёты менеджера (новые — в начале списка)."""
    return _read_history().get(str(user_id), [])[:HISTORY_LIMIT]


# ============================================================
# ВСПОМОГАТЕЛЬНОЕ
# ============================================================
def _log(user_id: int, event: str, payload: dict) -> None:
    # NEW: формат логов диалогов — как в bot.py (JSON в bot_dialogs.log)
    dialog_logger.info(json.dumps({
        "event": event,
        "user_id": user_id,
        **payload,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False))


def _is_exit(text: str) -> bool:
    # NEW: это выход, если текст — кнопка меню или слово отмены
    return text in MENU_BUTTONS or text.strip().lower() in EXIT_WORDS


# ============================================================
# КЛАВИАТУРЫ КАЛЬКУЛЯТОРА
# ============================================================
def _cancel_row() -> list[types.InlineKeyboardButton]:
    return [types.InlineKeyboardButton(text="❌ Отмена", callback_data="calc:cancel")]


def _models_kb(prices: dict) -> types.InlineKeyboardMarkup:
    # NEW: список моделей = ключи config/prices.json, у каждой — закупка из прайса
    rows: list[list[types.InlineKeyboardButton]] = []
    for code in sorted(prices, key=_model_sort_key):
        label = f"{code} — закупка {_fmt_money(prices[code]['cost'])}"
        rows.append([types.InlineKeyboardButton(text=label, callback_data=f"calc:model:{code}")])
    rows.append(_cancel_row())
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _cost_kb(base_price: float | None) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []
    if base_price is not None:
        rows.append([types.InlineKeyboardButton(
            text=f"✅ Базовая: {_fmt_money(base_price)}", callback_data="calc:cost:base",
        )])
    rows.append(_cancel_row())
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _cancel_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[_cancel_row()])


def _result_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔄 Новый расчёт", callback_data="calc:restart")],
        _cancel_row(),
    ])


def _history_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💰 Новый расчёт", callback_data="calc:restart")],
    ])


async def _edit(callback: types.CallbackQuery, text: str, kb: types.InlineKeyboardMarkup | None) -> None:
    # NEW: правим сообщение экрана; если оно уже не редактируется — шлём новое
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        system_logger.warning(f"Calc edit failed: {e}")
        try:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
        except Exception as e2:
            system_logger.warning(f"Calc answer failed: {e2}")


# ============================================================
# ТЕКСТЫ: РЕКОМЕНДАЦИЯ ПО СКИДКЕ, РЕЗУЛЬТАТ, ИСТОРИЯ
# ============================================================
def _discount_advice(cost: float, price: float, margin: float, margin_pct: float) -> str:
    """Рекомендация по скидке: безопасный предел — 30% маржи, вниз до 100 ₽."""
    if margin <= 0:
        return ("🚨 Цена продажи не покрывает закупку — убыток "
                f"{_fmt_money(abs(margin))}. Скидка исключена: поднимайте цену "
                "или предлагайте другую конфигурацию.")
    max_discount = int(margin * DISCOUNT_SHARE // 100) * 100
    disc_pct = max_discount / price * 100 if price else 0.0
    if margin_pct >= 30:
        return (f"✅ Запас по марже большой — можно смело торговаться. Безопасная скидка "
                f"до <b>{_fmt_money(max_discount)}</b> (~{_fmt_pct(disc_pct)} от цены).")
    if margin_pct >= 15:
        return (f"🟢 Маржа достаточная. Скидка — не больше <b>{_fmt_money(max_discount)}</b> "
                f"(~{_fmt_pct(disc_pct)} от цены); если клиент торгуется, отрабатывайте "
                "ценность: проверка сборки, сроки, гарантия.")
    if margin_pct >= 5:
        return (f"🟡 Маржа низкая. Максимум <b>{_fmt_money(max_discount)}</b> и только "
                "за объём или быструю оплату — иначе продавайте по цене.")
    return "🔴 Маржи почти нет — продавайте без скидки."


def _render_result(entry: dict, price_info: dict | None = None) -> str:
    advice = _discount_advice(entry["cost"], entry["price"], entry["margin"], entry["margin_pct"])
    review = _price_review(float(entry["price"]), float(entry["margin"]), price_info)
    return (
        "🧮 <b>Расчёт готов</b>\n\n"
        f"📦 Модель: <b>{html.escape(str(entry['model']))}</b>\n"
        f"🛒 Закупка: {_fmt_money(entry['cost'])}\n"
        f"💼 Продажа клиенту: {_fmt_money(entry['price'])}\n\n"
        f"📊 <b>Маржа: {_fmt_money(entry['margin'])}</b>\n"
        f"📈 <b>Маржинальность: {_fmt_pct(entry['margin_pct'])}</b> (от цены продажи)\n\n"
        "💡 <b>Рекомендация по скидке:</b>\n"
        f"{advice}"
        f"{review}"
    )


def _render_history(entries: list[dict]) -> str:
    if not entries:
        return ("📊 <b>История расчётов</b>\n\nПока пусто. Нажмите «💰 Калькулятор» — "
                "после расчёта он появится здесь (храню последние 5).")
    lines = ["📊 <b>История расчётов</b> — последние 5:\n"]
    for i, e in enumerate(entries, 1):
        try:
            stamp = datetime.fromisoformat(str(e.get("ts", ""))).strftime("%d.%m %H:%M")
        except Exception:
            stamp = "—"
        try:
            margin = float(e.get("margin", 0))
            margin_pct = float(e.get("margin_pct", 0))
        except (TypeError, ValueError):
            continue
        lines.append(
            f"{i}. {stamp} — {html.escape(str(e.get('model', '—')))}: "
            f"маржа {_fmt_money(margin)} ({_fmt_pct(margin_pct)})"
        )
    return "\n".join(lines)


# ============================================================
# ХЕНДЛЕРЫ: ВХОД В КАЛЬКУЛЯТОР И ИСТОРИЯ
# ============================================================
@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    # NEW: универсальная отмена — работает в калькуляторе и в других FSM-сценариях бота
    await state.clear()
    await message.answer("❌ Действие отменено.")


@router.message(F.text == CALC_BUTTON)
async def start_calculator(message: types.Message, state: FSMContext):
    # NEW: выходим из любого предыдущего сценария и показываем список моделей
    await state.clear()
    prices = _load_prices()
    if not prices:
        await message.answer(
            "⚠️ Не удалось прочитать <b>config/prices.json</b>.\n"
            "Добавьте файл с базовыми закупочными ценами и нажмите кнопку ещё раз.",
            parse_mode="HTML",
        )
        return
    _log(message.from_user.id, "calc_start", {})
    await message.answer(
        "🧮 <b>Калькулятор маржи</b>\n\nШаг 1/3 — выберите модель АКБ:",
        reply_markup=_models_kb(prices),
        parse_mode="HTML",
    )
    await state.set_state(MarginState.choosing_model)


@router.message(F.text == HISTORY_BUTTON)
async def show_history(message: types.Message, state: FSMContext):
    # выход из активного FSM-сценария (см. button_ttx в menu.py)
    await state.clear()
    entries = get_history(message.from_user.id)
    _log(message.from_user.id, "calc_history_view", {"count": len(entries)})
    await message.answer(
        _render_history(entries),
        reply_markup=_history_kb(),
        parse_mode="HTML",
    )


async def _exit_flow(message: types.Message, state: FSMContext) -> None:
    _log(message.from_user.id, "calc_cancel", {"via": "text"})
    await state.clear()
    await message.answer("❌ Калькулятор закрыт. Выберите раздел в меню и нажмите кнопку ещё раз.")


# ============================================================
# ХЕНДЛЕРЫ: CALLBACK-КНОПКИ («calc:»)
# ============================================================
@router.callback_query(F.data.startswith("calc:model:"), MarginState.choosing_model)
async def callback_choose_model(callback: types.CallbackQuery, state: FSMContext):
    code = callback.data.split(":", 2)[2]  # код может содержать пробелы: «8S2P XT-60»
    info = _load_prices().get(code) or {}
    base = info.get("cost")
    await state.update_data(model=code)
    await state.set_state(MarginState.waiting_for_cost)
    _log(callback.from_user.id, "calc_model_selected", {"model": code})
    head = f"📦 Модель: <b>{html.escape(code)}</b>"
    if info:
        # NEW: полный прайс модели из config — закупка, розница, опт, НДС, плановая маржа
        head += f"\n\n🏷 Прайс: {_price_ladder(info)}"
        if info.get("profit"):
            head += f"\n💰 Плановая маржа по прайсу: {_fmt_money(info['profit'])}"
    if base is not None:
        text = (f"{head}\n\n"
                f"Шаг 2/3 — закупочная цена. Базовая из конфига: <b>{_fmt_money(base)}</b>.\n"
                "Введите свою цену или нажмите кнопку ниже.")
    else:
        text = (f"{head}\n\n"
                "Шаг 2/3 — закупочная цена. Введите число, например <b>28000</b>.")
    await _edit(callback, text, _cost_kb(base))
    await callback.answer()


@router.callback_query(F.data.startswith("calc:model:"))
async def callback_choose_model_stale(callback: types.CallbackQuery):
    # NEW: нажатие после сброса состояния (в старом сообщении) — без падений
    await callback.answer("Сессия устарела — нажмите «💰 Калькулятор» заново", show_alert=True)


@router.callback_query(F.data == "calc:cost:base", MarginState.waiting_for_cost)
async def callback_cost_base(callback: types.CallbackQuery, state: FSMContext):
    # NEW: «берётся из config» — закупочная цена (поле cost прайса) одной кнопкой
    data = await state.get_data()
    code = data.get("model")
    info = _load_prices().get(code) or {}
    base = info.get("cost") if code else None
    if base is None:
        await callback.answer("Базовая цена не найдена — введите число", show_alert=True)
        return
    await state.update_data(cost=round(float(base), 2))
    await state.set_state(MarginState.waiting_for_price)
    _log(callback.from_user.id, "calc_cost_entered", {"model": code, "cost": float(base), "from_base": True})
    head = f"📦 Модель: <b>{html.escape(str(code))}</b>"
    if info:
        head += f"\n🏷 Прайс: {_price_ladder(info)}"
    await _edit(
        callback,
        f"{head}\n"
        f"🛒 Закупка (базовая): <b>{_fmt_money(base)}</b>\n\n"
        "Шаг 3/3 — цена продажи клиенту.\nВведите сумму:",
        _cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "calc:cost:base")
async def callback_cost_base_stale(callback: types.CallbackQuery):
    await callback.answer("Сессия устарела — нажмите «💰 Калькулятор» заново", show_alert=True)


@router.callback_query(F.data == "calc:restart")
async def callback_restart(callback: types.CallbackQuery, state: FSMContext):
    # NEW: «Новый расчёт» — с экрана результата или истории; сбрасывает прошлый сценарий
    await state.clear()
    prices = _load_prices()
    if not prices:
        await _edit(callback, "⚠️ Не удалось прочитать <b>config/prices.json</b>.", None)
        await callback.answer()
        return
    _log(callback.from_user.id, "calc_restart", {})
    await state.set_state(MarginState.choosing_model)
    await _edit(callback, "🧮 <b>Калькулятор маржи</b>\n\nШаг 1/3 — выберите модель АКБ:", _models_kb(prices))
    await callback.answer()


@router.callback_query(
    F.data == "calc:cancel",
    StateFilter(MarginState.choosing_model, MarginState.waiting_for_cost, MarginState.waiting_for_price),
)
async def callback_cancel(callback: types.CallbackQuery, state: FSMContext):
    _log(callback.from_user.id, "calc_cancel", {"via": "button"})
    await state.clear()
    await _edit(callback, "❌ Калькулятор закрыт.", None)
    await callback.answer()


@router.callback_query(F.data == "calc:cancel")
async def callback_cancel_stale(callback: types.CallbackQuery):
    await callback.answer("Калькулятор уже закрыт")


# ============================================================
# ХЕНДЛЕРЫ: ВВОД ЦЕН (FSM MarginState)
# ============================================================
@router.message(MarginState.waiting_for_cost, F.text)
async def process_cost_input(message: types.Message, state: FSMContext):
    raw = message.text or ""
    if _is_exit(raw):
        # NEW: нажатие кнопки меню/«отмена» во время ввода — чистый выход из FSM
        await _exit_flow(message, state)
        return
    cost = _parse_money(raw)
    if cost is None:
        await message.answer(
            "Нужно положительное число. Например: <b>28000</b>, <b>28 000</b> или <b>28к</b>.\n"
            "Или нажмите кнопку базовой цены выше.",
            parse_mode="HTML",
        )
        return
    data = await state.get_data()
    model = data.get("model", "—")
    await state.update_data(cost=round(cost, 2))
    await state.set_state(MarginState.waiting_for_price)
    _log(message.from_user.id, "calc_cost_entered", {"model": model, "cost": round(cost, 2)})
    await message.answer(
        f"🛒 Закупка: <b>{_fmt_money(cost)}</b>\n\n"
        "Шаг 3/3 — цена продажи клиенту.\nВведите сумму:",
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )


@router.message(MarginState.waiting_for_cost)
async def process_cost_not_text(message: types.Message):
    await message.answer("Отправьте закупочную цену текстом — числом, например <b>28000</b>.", parse_mode="HTML")


@router.message(MarginState.waiting_for_price, F.text)
async def process_price_input(message: types.Message, state: FSMContext):
    raw = message.text or ""
    if _is_exit(raw):
        await _exit_flow(message, state)
        return
    price = _parse_money(raw)
    if price is None:
        await message.answer(
            "Нужно положительное число. Например: <b>35000</b> или <b>35 000</b>.",
            parse_mode="HTML",
        )
        return
    data = await state.get_data()
    model = data.get("model", "—")
    try:
        cost = float(data.get("cost") or 0)
    except (TypeError, ValueError):
        cost = 0.0
    if cost <= 0:
        # NEW: сессия потеряна (перезапуск бота на MemoryStorage) — честно перезапускаем сценарий
        await state.clear()
        await message.answer("Сессия расчёта устарела. Нажмите «💰 Калькулятор» заново.")
        return
    margin = round(price - cost, 2)
    margin_pct = round(margin / price * 100, 1) if price else 0.0
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "cost": round(cost, 2),
        "price": round(price, 2),
        "margin": margin,
        "margin_pct": margin_pct,
    }
    await add_history_entry(message.from_user.id, entry)
    await state.clear()  # расчёт завершён — состояние больше не нужно
    _log(message.from_user.id, "calc_result", {k: entry[k] for k in ("model", "cost", "price", "margin", "margin_pct")})
    # NEW: сверка результата с прайсом модели (если модель есть в config/prices.json)
    await message.answer(_render_result(entry, _load_prices().get(str(model))), reply_markup=_result_kb(), parse_mode="HTML")


@router.message(MarginState.waiting_for_price)
async def process_price_not_text(message: types.Message):
    await message.answer("Отправьте цену продажи текстом — числом, например <b>35000</b>.", parse_mode="HTML")


@router.message(MarginState.choosing_model)
async def choosing_model_hint(message: types.Message, state: FSMContext):
    # NEW: во время выбора модели ждём нажатие кнопки; текст/фото — подсказка
    if message.text and _is_exit(message.text):
        await _exit_flow(message, state)
        return
    await message.answer("Выберите модель кнопкой из списка выше или нажмите «❌ Отмена».")





