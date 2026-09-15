"""
Модуль «❓ FAQ» — база знаний менеджера: частые вопросы клиентов по продажам.

Сценарий:
  «❓ FAQ» (reply)       — список категорий + кнопка поиска;
  faq:c:<i>             — вопросы категории i;
  faq:q:<i>:<j>         — ответ на вопрос j из категории i (кнопки «назад»);
  faq:search            — FSM FaqState.waiting_for_query: менеджер вводит
                          ключевое слово → бот показывает подходящие вопросы
                          (подстрока в вопросе/ответе, без учёта регистра,
                          ранжирование: вопрос ×2, ответ ×1);
  faq:menu / faq:cancel — к категориям / закрыть.

Данные: data/faq.json — {"categories": [{"name", "questions": [{"q","a"}]}]}.
Файл перечитывается при каждом обращении — правки подхватываются без
перезапуска бота; битые записи пропускаются с предупреждением в system-log.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from keyboards import MAIN_BUTTONS

router = Router(name="faq")

dialog_logger = logging.getLogger("dialog")
system_logger = logging.getLogger("system")

BASE_DIR = Path(__file__).resolve().parent.parent
FAQ_PATH = BASE_DIR / "data" / "faq.json"

FAQ_BUTTON = "❓ FAQ"
SEARCH_LIMIT = 10      # максимум вопросов в результатах поиска
BTN_TEXT_LIMIT = 60    # обрезка текста вопроса в кнопке

# Кнопки меню, по которым можно выйти из поиска (сама «❓ FAQ» — перезапуск)
MENU_BUTTONS = frozenset(MAIN_BUTTONS) - {FAQ_BUTTON}
EXIT_WORDS = frozenset({"отмена", "cancel", "❌", "❌ отмена"})


class FaqState(StatesGroup):
    waiting_for_query = State()   # шаг: менеджер вводит ключевое слово


# ============================================================
# ДАННЫЕ: data/faq.json
# ============================================================
def _load_faq() -> list[dict]:
    """Читает категории FAQ; битые записи пропускает с предупреждением."""
    try:
        with open(FAQ_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        system_logger.warning(f"faq.json не найден: {FAQ_PATH}")
        return []
    except Exception as e:
        system_logger.warning(f"faq.json read failed: {e}")
        return []

    if not isinstance(raw, dict):
        system_logger.warning("faq.json: корень не объект — пропущен")
        return []

    categories = []
    for cat in raw.get("categories", []):
        if not isinstance(cat, dict) or not cat.get("name"):
            system_logger.warning("faq.json: категория без name — пропущена")
            continue
        questions = []
        for q in cat.get("questions", []):
            if isinstance(q, dict) and q.get("q") and q.get("a"):
                questions.append({"q": str(q["q"]), "a": str(q["a"])})
            else:
                system_logger.warning(f"faq.json: вопрос без q/a в «{cat['name']}» — пропущен")
        categories.append({"name": str(cat["name"]), "questions": questions})
    return categories


# ============================================================
# ПОИСК ПО FAQ
# ============================================================
def _find(query: str, categories: list[dict]) -> list[tuple[int, int, dict]]:
    """Вопросы, релевантные запросу: [(индекс категории, индекс, запись)].

    Каждое слово запроса (≥3 символов, без учёта регистра): +2 за вхождение
    в вопрос, +1 — в ответ. Результат — по убыванию релевантности,
    не более SEARCH_LIMIT записей.
    """
    words = re.findall(r"[а-яёa-z0-9]{3,}", (query or "").lower())
    if not words:
        return []
    matches = []
    for ci, cat in enumerate(categories):
        for qi, item in enumerate(cat["questions"]):
            q, a = item["q"].lower(), item["a"].lower()
            score = sum(2 * (w in q) + (w in a) for w in words)
            if score > 0:
                matches.append((score, ci, qi, item))
    matches.sort(key=lambda m: -m[0])
    return [(ci, qi, item) for _, ci, qi, item in matches[:SEARCH_LIMIT]]


# ============================================================
# РЕНДЕР ЭКРАНОВ
# ============================================================
def _cut(text: str, limit: int = BTN_TEXT_LIMIT) -> str:
    """Обрезка текста вопроса под ширину кнопки."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _render_main(categories: list[dict]) -> str:
    total = sum(len(c["questions"]) for c in categories)
    lines = [f"❓ <b>FAQ</b> — частые вопросы клиентов ({total})\n"]
    for cat in categories:
        lines.append(f"• {cat['name']} — {len(cat['questions'])} вопр.")
    lines.append("\nВыберите категорию или найдите вопрос по слову.")
    return "\n".join(lines)


def _render_category(cat: dict) -> str:
    lines = [f"❓ <b>FAQ — {cat['name']}</b>\n"]
    for i, item in enumerate(cat["questions"], 1):
        lines.append(f"{i}. {item['q']}")
    lines.append("\nНажмите на вопрос ниже — покажу ответ.")
    return "\n".join(lines)


def _render_answer(cat: dict, item: dict) -> str:
    return (f"❓ <b>{item['q']}</b>\n\n"
            f"{item['a']}\n\n"
            f"🗂 Категория: {cat['name']}")


def _render_search_results(query: str, matches: list[tuple[int, int, dict]],
                           categories: list[dict]) -> str:
    if not matches:
        return (f"🔎 Поиск по FAQ: «{query}»\n\n"
                "Ничего не нашёл. Попробуйте другое слово — например:\n"
                "<i>гарантия, заряд, хранение, мороз, доставка, оплата, юрлица, скидка</i>")
    lines = [f"🔎 <b>Поиск по FAQ</b>: «{query}» — найдено {len(matches)}:\n"]
    for ci, qi, item in matches:
        lines.append(f"• [{categories[ci]['name']}] {item['q']}")
    lines.append("\nНажмите на вопрос ниже — покажу ответ.")
    return "\n".join(lines)


# ============================================================
# КЛАВИАТУРЫ (inline)
# ============================================================
def _main_kb(categories: list[dict]) -> types.InlineKeyboardMarkup:
    rows = [[types.InlineKeyboardButton(
        text=f"{cat['name']} ({len(cat['questions'])})",
        callback_data=f"faq:c:{ci}")]
        for ci, cat in enumerate(categories)]
    rows.append([types.InlineKeyboardButton(text="🔎 Поиск по FAQ", callback_data="faq:search")])
    rows.append([types.InlineKeyboardButton(text="❌ Закрыть", callback_data="faq:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _category_kb(ci: int, cat: dict) -> types.InlineKeyboardMarkup:
    rows = [[types.InlineKeyboardButton(
        text=_cut(item["q"]),
        callback_data=f"faq:q:{ci}:{qi}")]
        for qi, item in enumerate(cat["questions"])]
    rows.append([types.InlineKeyboardButton(text="⬅️ Категории", callback_data="faq:menu"),
                 types.InlineKeyboardButton(text="❌ Закрыть", callback_data="faq:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _answer_kb(ci: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ К вопросам", callback_data=f"faq:c:{ci}"),
         types.InlineKeyboardButton(text="🗂 Категории", callback_data="faq:menu")],
        [types.InlineKeyboardButton(text="🔎 Поиск по FAQ", callback_data="faq:search"),
         types.InlineKeyboardButton(text="❌ Закрыть", callback_data="faq:cancel")],
    ])


def _search_kb(matches: list[tuple[int, int, dict]]) -> types.InlineKeyboardMarkup:
    rows = [[types.InlineKeyboardButton(
        text=_cut(item["q"]),
        callback_data=f"faq:q:{ci}:{qi}")]
        for ci, qi, item in matches]
    rows.append([types.InlineKeyboardButton(text="🗂 Категории", callback_data="faq:menu"),
                 types.InlineKeyboardButton(text="❌ Закрыть", callback_data="faq:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _prompt_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="faq:cancel")],
    ])


# ============================================================
# ХЕЛПЕРЫ
# ============================================================
def _log(user_id: int, event: str, payload: dict | None = None) -> None:
    """Событие в dialog-log."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fields = " ".join(f"{k}={v}" for k, v in (payload or {}).items())
    dialog_logger.info(f"[{ts}] user={user_id} event={event}{(' ' + fields) if fields else ''}")


async def _edit(callback: types.CallbackQuery, text: str,
                kb: types.InlineKeyboardMarkup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:  # «message is not modified» и т.п. — не роняем поток
        system_logger.warning(f"faq edit failed: {e}")


def _is_exit(text: str) -> bool:
    """Текст — кнопка меню или слово выхода?"""
    t = (text or "").strip()
    return t in MENU_BUTTONS or t.lower() in EXIT_WORDS


def _parse_index(raw: str, part: int) -> int | None:
    """Достаёт неотрицательный индекс из callback.data по позиции part."""
    try:
        value = int(raw.split(":")[part])
    except (IndexError, ValueError):
        return None
    return value if value >= 0 else None


# ============================================================
# ХЕНДЛЕРЫ
# ============================================================
@router.message(F.text == FAQ_BUTTON)
async def button_faq(message: types.Message, state: FSMContext) -> None:
    """«❓ FAQ» — главный экран: категории + поиск. Перезапускает экран,
    даже если был активен поиск (хендлер зарегистрирован раньше FSM)."""
    await state.clear()
    _log(message.from_user.id, "faq_open", {})
    categories = _load_faq()
    await message.answer(
        _render_main(categories),
        reply_markup=_main_kb(categories),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "faq:menu")
async def callback_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    """⬅️ Категории — к главному экрану FAQ."""
    await state.clear()
    _log(callback.from_user.id, "faq_menu", {})
    categories = _load_faq()
    await _edit(callback, _render_main(categories), _main_kb(categories))
    await callback.answer()


@router.callback_query(F.data.startswith("faq:c:"))
async def callback_category(callback: types.CallbackQuery) -> None:
    """faq:c:<i> — список вопросов категории."""
    ci = _parse_index(callback.data, 2)
    if ci is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    categories = _load_faq()
    if ci >= len(categories):
        await callback.answer("Категория не найдена", show_alert=True)
        return
    cat = categories[ci]
    _log(callback.from_user.id, "faq_category", {"name": cat["name"]})
    await _edit(callback, _render_category(cat), _category_kb(ci, cat))
    await callback.answer()


@router.callback_query(F.data.startswith("faq:q:"))
async def callback_question(callback: types.CallbackQuery) -> None:
    """faq:q:<i>:<j> — ответ на вопрос."""
    ci = _parse_index(callback.data, 2)
    qi = _parse_index(callback.data, 3)
    if ci is None or qi is None:
        await callback.answer("Вопрос не найден", show_alert=True)
        return
    categories = _load_faq()
    if ci >= len(categories) or qi >= len(categories[ci]["questions"]):
        await callback.answer("Вопрос не найден", show_alert=True)
        return
    cat = categories[ci]
    item = cat["questions"][qi]
    _log(callback.from_user.id, "faq_answer", {"q": item["q"]})
    await _edit(callback, _render_answer(cat, item), _answer_kb(ci))
    await callback.answer()


@router.callback_query(F.data == "faq:search")
async def callback_search(callback: types.CallbackQuery, state: FSMContext) -> None:
    """🔎 Поиск по FAQ — запрос ключевого слова (FSM)."""
    await state.set_state(FaqState.waiting_for_query)
    await _edit(callback,
                "🔎 <b>Поиск по FAQ</b>\n\n"
                "Введите слово или фразу одним сообщением, например:\n"
                "<i>гарантия, мороз, юрлица, зарядка</i>",
                _prompt_kb())
    await callback.answer()


@router.message(FaqState.waiting_for_query, F.text)
async def process_search_text(message: types.Message, state: FSMContext) -> None:
    """Ключевое слово → релевантные вопросы FAQ. Любая кнопка меню,
    слово выхода или повторное «❓ FAQ» — выход/перезапуск."""
    raw = (message.text or "").strip()
    if raw == FAQ_BUTTON:  # повторное нажатие — перезапуск экрана
        await state.clear()
        await button_faq(message, state)
        return
    if _is_exit(raw):
        _log(message.from_user.id, "faq_cancel", {"via": "text"})
        await state.clear()
        await message.answer("❌ FAQ закрыт. Выберите раздел в меню.")
        return
    if not raw:
        await message.answer("🔎 Пустой запрос. Введите ключевое слово текстом.")
        return
    categories = _load_faq()
    matches = _find(raw, categories)
    _log(message.from_user.id, "faq_search", {"query": raw, "found": len(matches)})
    await state.clear()
    await message.answer(
        _render_search_results(raw, matches, categories),
        reply_markup=_search_kb(matches),
        parse_mode="HTML",
    )


@router.message(FaqState.waiting_for_query)
async def search_not_text(message: types.Message, state: FSMContext) -> None:
    """Во время поиска прислали не текст (фото и т.п.) — мягкая подсказка."""
    _log(message.from_user.id, "faq_search_non_text", {})
    await message.answer("🔎 Жду ключевое слово текстом — одним сообщением.")


@router.callback_query(F.data == "faq:cancel")
async def callback_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    """❌ Закрыть — очистить FSM и свернуть экран."""
    _log(callback.from_user.id, "faq_cancel", {"via": "button"})
    await state.clear()
    try:
        await callback.message.edit_text("❌ FAQ закрыт. Выберите раздел в меню.")
    except Exception as e:
        system_logger.warning(f"faq cancel edit failed: {e}")
    await callback.answer()