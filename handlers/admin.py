"""
Админ-команды бота (аудит/развитие пп. 3.1–3.2): аналитика и CRUD без БД-клиента.

Команды (доступны только ADMIN_ID из .env; не-админ получает тишину —
как у /health в handlers/fallback.py):

  /stats        — 📊 аналитика: всего пользователей, активных за 7 дней,
                  расчётов маржи за сегодня (таблица calc_entries), обращений
                  к AI за сегодня (daily_counters: 'ai_ask' + 'ai_voice');
  /add_faq      — ➕ FSM «вопрос → ответ → категория»: добавляет вопрос в FAQ
                  (существующая категория — без учёта регистра, новая — в конец
                  списка). Текст экранируется (html.escape): FAQ рендерится с
                  parse_mode="HTML", сырые «<>&» ломали бы экран вопроса;
  /update_price — 🏷 FSM «код модели → новая цена»: меняет ступень прайса.
                  Формат ввода: «35000» — розница (cash, базовая продажная
                  цена) или «опт=32000» / «закупка=12000» — конкретная ступень
                  (подписи — PRICE_FIELDS калькулятора);
  /cancel       — ❌ отмена активного админ-FSM (и слово «отмена» в шагах).

Конструктор дронов — админ-контур (Этап 8; только ADMIN_ID, не-админ — тишина):

  /load_stock         — 📥 импорт остатков: FSM-шаг ждёт CSV-документ
                        (cp1251/utf-8, «Номенклатура;Ед;Кол-во»); байты —
                        message.download → временный файл
                        (NamedTemporaryFile, unlink в finally) →
                        import_stock_csv; отчёт: позиции/новые/обнулено/
                        пропущено, снапшот (list_snapshots(limit=2)),
                        топ-5 изменений остатков (diff_snapshots) и
                        подсказка /classify_pending при нераспознанных;
  /classify_pending   — 🧾 нераспознанные позиции (parts.category="other");
  /add_alias          — 🏷 разметка «<категория> <название>»: upsert
                        part_aliases + reclassify_all_parts (+commit);
  /aliases, /del_alias — список алиасов / удаление по id;
  /compat_rules       — ⚙️ правила совместимости: список (✅/⛔) + клавиатура
                        kb_compat_rules: cr:toggle:<id> — вкл/выкл с
                        перерисовкой (edit_or_send), cr:add — FSM
                        rule_type → params (JSON-объект, иначе шаг
                        повторяется) → описание (пусто/«-» — без описания);
  /stock_stats        — 📊 расход между двумя последними снапшотами:
                        убывания/пополнения (шт и позиций), новые/исчезли,
                        топ-5 изменений;
  /reclassify         — ♻️ пересчёт category/params всех позиций
                        (reclassify_all_parts + commit) + список «other»;
  /recipes            — 📦 серийные конфигурации (Этап 10): по каждому
                        рецепту «{title} — можно собрать: N шт»
                        (recipe_builds по текущим остаткам) и ⚠️-список
                        слотов с part_key=None (имя из 1С не нашлось в
                        каталоге на момент сида);
  /stats              — + блок «🛠 Конструктор»: частей всего/с остатком,
                        категорий, последний снапшот, черновики.

Роутер подключается в bot.py ПЕРВЫМ: админ-команды и шаги админ-FSM не должны
перехватываться FSM других сценариев (калькулятор ловит любой текст в своих
состояниях) и fallback'ом AI.

Данные берутся из пакета db (синглтоны users/counters/faq/prices/calc_history) —
никаких прямых SQL/JSON-правок; конструкторский контур — фасады
db.repositories (реэкспорт db.repositories_builder) и db.stock_import.
Список FAQ/прайс перечитывается при обращении: правки подхватываются без
перезапуска бота.
"""

import html
import io
import json
import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import func, select
from aiogram import Bot
from config import ADMIN_ID
from db import (
    calc_history as history_db,
    counters as counters_db,
    faq as faq_db,
    prices as prices_db,
    users as users_db,
)
from db.engine import get_sessionmaker
from db.models import PART_CATEGORIES, BuilderDraft, Stock
from db.repositories import (
    add_alias,
    add_compat_rule,
    count_by_category,
    delete_alias,
    diff_snapshots,
    list_aliases,
    list_compat_rules,
    list_parts,
    list_recipes,
    list_snapshots,
    seed_recipes,
    set_compat_rule_active,
)
from db.stock_import import import_stock_csv, reclassify_all_parts
from data.builder.categories import CATEGORY_LABELS
from handlers.calculator import PRICE_FIELDS, _parse_money
from keyboards.builder import kb_compat_rules
from services.builder.recipes import recipe_builds
from utils.ui import edit_or_send
from utils.ui import safe_answer

router = Router(name="admin")

dialog_logger = logging.getLogger("dialog")
system_logger = logging.getLogger("system")

ACTIVE_DAYS = 7                # окно «активных пользователей» для /stats
DEFAULT_PRICE_FIELD = "cash"   # ступень «розница» — базовая продажная цена
EXIT_WORDS = frozenset({"отмена", "cancel", "❌", "❌ отмена"})

# Конструктор (Этап 8): лимиты и callback-префикс клавиатуры kb_compat_rules.
BUILDER_OTHER_LIMIT = 20            # максимум строк в /classify_pending
STOCK_DIFF_TOP = 5                  # топ изменений остатков в отчётах
COMPAT_TOGGLE_PREFIX = "cr:toggle:"  # toggle-кнопки kb_compat_rules

# Обратный маппинг «категория по-русски → машинный ключ» (Этап 9): русские
# подписи CATEGORY_LABELS без учёта регистра и «ё», первые слова подписей,
# бытовые сокращения — плюс латинские ключи PART_CATEGORIES (обратная
# совместимость для старых сценариев разметки).
_CATEGORY_BY_LABEL: dict[str, str] = {
    key.lower().replace("ё", "е"): key for key in CATEGORY_LABELS
}
for _key, _label in CATEGORY_LABELS.items():
    _norm = _label.lower().replace("ё", "е")
    _CATEGORY_BY_LABEL[_norm] = _key
    _CATEGORY_BY_LABEL.setdefault(_norm.split()[0], _key)
for _alias, _key in (
    ("мотор", "motor"), ("проп", "prop"), ("пропеллер", "prop"),
    ("стек", "fc_esc_stack"), ("акб", "battery"), ("батарея", "battery"),
    ("антенна", "antenna_air"), ("камера", "camera"),
):
    _CATEGORY_BY_LABEL.setdefault(_alias, _key)


def _category_key(raw: str) -> str | None:
    """Категория из слова команды /add_alias → машинный ключ (нет — None)."""
    return _CATEGORY_BY_LABEL.get(str(raw or "").strip().lower().replace("ё", "е"))

# Псевдонимы ступеней прайса для «поле=цена»: русские подписи калькулятора
# (без учёта регистра/пробелов) + английские ключи таблицы prices.
FIELD_ALIASES: dict[str, str] = {key: key for key, _ in PRICE_FIELDS}
for _key, _label in PRICE_FIELDS:
    FIELD_ALIASES[_label.lower().replace(" ", "")] = _key
    FIELD_ALIASES[_label.lower().replace(" ", "").rstrip("%")] = _key


class AdminState(StatesGroup):
    """FSM админ-команд (/add_faq — 3 шага, /update_price — 2 шага,
    /load_stock — 1 шаг с документом, «➕» в /compat_rules — 3 шага)."""

    faq_question = State()   # /add_faq шаг 1: текст вопроса
    faq_answer = State()     # /add_faq шаг 2: текст ответа
    faq_category = State()   # /add_faq шаг 3: категория
    price_code = State()     # /update_price шаг 1: код модели
    price_value = State()    # /update_price шаг 2: новая цена
    waiting_stock_file = State()   # /load_stock: ждём CSV-документ
    waiting_rule_type = State()    # compat-правило, шаг 1: rule_type
    waiting_rule_params = State()  # compat-правило, шаг 2: params (JSON-объект)
    waiting_rule_desc = State()    # compat-правило, шаг 3: описание


def _is_admin(message: types.Message) -> bool:
    """Команды видит только ADMIN_ID (0/не задан — никому)."""
    return bool(ADMIN_ID) and message.from_user is not None \
        and message.from_user.id == ADMIN_ID


def _esc(text: str) -> str:
    """Экранирование пользовательского текста для ответов с parse_mode="HTML"."""
    return html.escape(text or "")


def _cb_is_admin(callback: types.CallbackQuery) -> bool:
    """Callback-эквивалент _is_admin (не-админ — тишина, даже без answer())."""
    return bool(ADMIN_ID) and callback.from_user is not None \
        and callback.from_user.id == ADMIN_ID


def _fmt_qty(quantity) -> str:
    """Количество для отчётов: 8.0 → «8», 2.5 → «2.5» (без хвостовых нулей)."""
    try:
        return f"{float(quantity or 0):g}"
    except (TypeError, ValueError):
        return "0"


def _fmt_dt(iso: str | None) -> str:
    """ISO-метка снапшота → «ДД.ММ ЧЧ:ММ» (для /stock_stats и /stats)."""
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d.%m %H:%M")
    except ValueError:
        return str(iso)[:16].replace("T", " ")


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русская форма числа: _plural(1, "позиция", "позиции", "позиций")."""
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def _diff_line(row: dict) -> str:
    """Строка «имя: old → new (±d)» для отчётов по снапшотам."""
    old, new = float(row["old"]), float(row["new"])
    sign = "+" if new > old else "-"
    name = _esc(str(row.get("part_name") or row.get("part_key") or "?"))
    return f"  • {name}: {_fmt_qty(old)} → {_fmt_qty(new)} ({sign}{_fmt_qty(abs(new - old))})"


def _top_changes(changes: list[dict], limit: int = STOCK_DIFF_TOP) -> list[str]:
    """Топ изменений по модулю дельты (diff_snapshots → "changed")."""
    ranked = sorted(
        changes,
        key=lambda r: abs(float(r["new"]) - float(r["old"])),
        reverse=True,
    )
    return [_diff_line(r) for r in ranked[:limit]]


def _is_exit(raw: str) -> bool:
    return raw.strip().lower() in EXIT_WORDS


def _price_line(entry: dict) -> str:
    """«закупка 12000 · розница 15000 · …» (только заполненные ступени)."""
    parts = []
    for key, label in PRICE_FIELDS:
        if key in entry and entry[key] is not None:
            parts.append(f"{label} {float(entry[key]):g}")
    return " · ".join(parts) if parts else "(прайс пуст)"


def _lookup_field(raw: str) -> str | None:
    """Псевдоним ступени прайса → ключ таблицы prices; нет — None."""
    return FIELD_ALIASES.get(raw.strip().lower().replace(" ", ""))


async def _not_command(message: types.Message) -> None:
    """Неизвестная команда пришла в шаге ввода: не сохраняем её как текст."""
    await message.answer(
        "Команды во время ввода не поддерживаются. /cancel — отмена."
    )


# ============================================================
# /cancel — отмена админ-FSM. Регистрируется ДО шаговых хендлеров:
# в aiogram первый совпавший хендлер забирает событие, и иначе
# «/cancel» был бы сохранён как текст вопроса/цены.
# ============================================================
@router.message(Command("cancel"), StateFilter(AdminState))
async def cmd_cancel(message: types.Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await _cancel(message, state)


async def _cancel(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Отменено.")


async def _builder_block() -> str:
    """Блок «🛠 Конструктор» для /stats: части, остатки, снапшот, черновики.

    Count-запросы (остаток, черновики) — ORM по образцу admin.py (фасада
    «количество черновиков всех пользователей» нет); остальное — фасады
    db.repositories (count_by_category, list_snapshots).
    """
    counts = await count_by_category()
    async with get_sessionmaker()() as session:
        in_stock = await session.scalar(
            select(func.count()).select_from(Stock).where(Stock.quantity > 0)
        )
        drafts = await session.scalar(select(func.count()).select_from(BuilderDraft))
    snaps = await list_snapshots(limit=1)
    snap = f"#{snaps[0]['id']} от {_fmt_dt(snaps[0]['loaded_at'])}" if snaps else "нет"
    return (
        "\n\n🛠 <b>Конструктор</b>\n"
        f"Частей всего: <b>{sum(counts.values())}</b>"
        f" (с остатком: <b>{int(in_stock or 0)}</b>),"
        f" категорий: <b>{len(counts)}</b>\n"
        f"Выгрузка остатков: {snap}\n"
        f"Черновиков: <b>{int(drafts or 0)}</b>"
    )


# ============================================================
# /stats — аналитика (п. 3.1) + блок «Конструктор» (Этап 8)
# ============================================================
@router.message(Command("stats"))
async def cmd_stats(message: types.Message) -> None:
    """📊 Пользователи, активность, расчёты и AI-нагрузка за сегодня."""
    if not _is_admin(message):
        return
    now = datetime.now()
    total = await users_db.count_all()
    active = await users_db.count_active_since(now - timedelta(days=ACTIVE_DAYS))
    calcs_today = await history_db.count_since(now.replace(
        hour=0, minute=0, second=0, microsecond=0))
    ai_text = await counters_db.value("ai_ask")
    ai_voice = await counters_db.value("ai_voice")
    faq_cats, faq_questions = await faq_db.counts()
    builder = await _builder_block()
    await message.answer(
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей всего: <b>{total}</b>\n"
        f"🟢 Активных за {ACTIVE_DAYS} дней: <b>{active}</b>\n"
        f"🧮 Расчётов маржи сегодня: <b>{calcs_today}</b>\n"
        f"🤖 Обращений к AI сегодня: <b>{ai_text + ai_voice}</b>\n"
        f"<i>(текст: {ai_text}, голос: {ai_voice})</i>\n\n"
        f"❓ FAQ: {faq_cats} категорий, {faq_questions} вопросов"
        + builder,
        parse_mode="HTML",
    )


# ============================================================
# /add_faq — FSM «вопрос → ответ → категория» (п. 3.2)
# ============================================================
@router.message(Command("add_faq"))
async def cmd_add_faq(message: types.Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.set_state(AdminState.faq_question)
    await message.answer(
        "➕ <b>Новый вопрос FAQ</b> — шаг 1/3.\n"
        "Введите текст вопроса одним сообщением.\n\n"
        "/cancel — отмена.",
        parse_mode="HTML",
    )


@router.message(AdminState.faq_question, F.text)
async def process_faq_question(message: types.Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if _is_exit(raw):
        await _cancel(message, state)
        return
    if raw.startswith("/"):
        await _not_command(message)
        return
    if not raw:
        await message.answer("Вопрос не может быть пустым. Введите текст вопроса.")
        return
    await state.update_data(question=_esc(raw))   # FAQ рендерится в HTML
    await state.set_state(AdminState.faq_answer)
    await message.answer(
        f"❓ Вопрос: <i>{_esc(raw)}</i>\n\n"
        "Шаг 2/3 — введите текст ответа одним сообщением.",
        parse_mode="HTML",
    )


@router.message(AdminState.faq_question)
async def faq_question_not_text(message: types.Message) -> None:
    await message.answer("Жду текст вопроса — отправьте его сообщением.")


@router.message(AdminState.faq_answer, F.text)
async def process_faq_answer(message: types.Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if _is_exit(raw):
        await _cancel(message, state)
        return
    if raw.startswith("/"):
        await _not_command(message)
        return
    if not raw:
        await message.answer("Ответ не может быть пустым. Введите текст ответа.")
        return
    await state.update_data(answer=_esc(raw))     # FAQ рендерится в HTML
    await state.set_state(AdminState.faq_category)
    categories = await faq_db.load()
    names = " | ".join(_esc(c["name"]) for c in categories) or "(пока нет категорий)"
    await message.answer(
        f"💬 Ответ: <i>{_esc(raw)}</i>\n\n"
        "Шаг 3/3 — категория. Введите название:\n"
        "существующая (регистр не важен) — вопрос добавится в неё,\n"
        "новая — будет создана в конце списка FAQ.\n\n"
        f"Текущие категории: {names}",
        parse_mode="HTML",
    )


@router.message(AdminState.faq_answer)
async def faq_answer_not_text(message: types.Message) -> None:
    await message.answer("Жду текст ответа — отправьте его сообщением.")


@router.message(AdminState.faq_category, F.text)
async def process_faq_category(message: types.Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if _is_exit(raw):
        await _cancel(message, state)
        return
    if raw.startswith("/"):
        await _not_command(message)
        return
    data = await state.get_data()
    try:
        created, total = await faq_db.add_question(
            raw, data.get("question", ""), data.get("answer", ""))
    except ValueError as e:
        await message.answer(f"⚠️ {e} Введите название категории текстом.")
        return
    await state.clear()
    where = "создана новая категория" if created else "существующая категория"
    dialog_logger.info(json.dumps({
        "event": "admin_add_faq",
        "user_id": message.from_user.id,
        "category": raw,
        "new_category": created,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False))
    await message.answer(
        f"✅ Вопрос добавлен в FAQ ({where} «{_esc(raw)}»,\n"
        f"вопросов в категории: {total}).",
        parse_mode="HTML",
    )


@router.message(AdminState.faq_category)
async def faq_category_not_text(message: types.Message) -> None:
    await message.answer("Жду название категории — отправьте его сообщением.")


# ============================================================
# /update_price — FSM «код модели → новая цена» (п. 3.2)
# ============================================================
@router.message(Command("update_price"))
async def cmd_update_price(message: types.Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.set_state(AdminState.price_code)
    await message.answer(
        "🏷 <b>Изменение цены</b> — шаг 1/2.\n"
        "Введите код модели (как в прайсе, например <b>6S2P</b>).\n\n"
        "/cancel — отмена.",
        parse_mode="HTML",
    )


@router.message(AdminState.price_code, F.text)
async def process_price_code(message: types.Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if _is_exit(raw):
        await _cancel(message, state)
        return
    if raw.startswith("/"):
        await _not_command(message)
        return
    if not raw:
        await message.answer("Код модели не может быть пустым.")
        return
    entry = await prices_db.get(raw)
    code = raw
    if entry is None and raw.upper() != raw:      # код введён не в том регистре
        entry = await prices_db.get(raw.upper())
        code = raw.upper()
    if entry is None:
        all_prices = await prices_db.load()
        known = ", ".join(list(all_prices)[:15]) or "(прайс пуст)"
        tail = " …" if len(all_prices) > 15 else ""
        await message.answer(
            f"Модель «{_esc(raw)}» не найдена в прайсе.\n"
            f"Доступные коды: {known}{tail}\n"
            "Введите код ещё раз или /cancel.",
            parse_mode="HTML",
        )
        return
    await state.update_data(code=code)
    await state.set_state(AdminState.price_value)
    await message.answer(
        f"📦 Модель <b>{_esc(code)}</b>: {_price_line(entry)}\n\n"
        "Шаг 2/2 — новая цена. Форматы:\n"
        "  <b>35000</b> — розница (основная продажная цена);\n"
        "  <b>опт=32000</b>, <b>закупка=12000</b>, <b>НДС 5%=14100</b> — ступень.\n"
        "Ступени: закупка, розница, опт, НДС 5%, НДС 22%.",
        parse_mode="HTML",
    )


@router.message(AdminState.price_code)
async def price_code_not_text(message: types.Message) -> None:
    await message.answer("Жду код модели текстом — отправьте его сообщением.")


@router.message(AdminState.price_value, F.text)
async def process_price_value(message: types.Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if _is_exit(raw):
        await _cancel(message, state)
        return
    if raw.startswith("/"):
        await _not_command(message)
        return
    field, amount_raw = DEFAULT_PRICE_FIELD, raw
    if "=" in raw:                                # «опт=32000» / «cash=35000»
        left, right = raw.split("=", 1)
        field = _lookup_field(left)
        amount_raw = right.strip()
        if field is None:
            await message.answer(
                "Не знаю такую ступень. Доступны: закупка, розница, опт, "
                "НДС 5%, НДС 22% (или ключи таблицы: cost, cash, wholesale, "
                "vat5, vat22)."
            )
            return
    value = _parse_money(amount_raw)
    if value is None:
        await message.answer(
            "Нужно положительное число. Например: <b>35000</b>, "
            "<b>35 000</b> или <b>35к</b>.",
            parse_mode="HTML",
        )
        return
    code = (await state.get_data()).get("code", "")
    if not await prices_db.set_field(code, field, value):
        await state.clear()
        await message.answer("Модель не найдена в прайсе (удалена?). /update_price — заново.")
        return
    entry = await prices_db.get(code) or {}
    await state.clear()
    dialog_logger.info(json.dumps({
        "event": "admin_update_price",
        "user_id": message.from_user.id,
        "model": code,
        "field": field,
        "value": value,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False))
    await message.answer(
        f"✅ Цена обновлена: <b>{_esc(code)}</b> — {_price_line(entry)}",
        parse_mode="HTML",
    )


@router.message(AdminState.price_value)
async def price_value_not_text(message: types.Message) -> None:
    await message.answer("Жду новую цену текстом — числом, например <b>35000</b>.",
                         parse_mode="HTML")


# ============================================================
# /cancel — слово «отмена» в шагах ловится через _cancel выше по коду
# ============================================================


# ============================================================
# «🛠 КОНСТРУКТОР ДРОНОВ»: админ-контур (Этап 8).
# Роутер первый: не-админ — тишина, шаги FSM не перехватываются
# сценариями и fallback'ом AI.
# ============================================================

# ------------------------------------------------------------
# /load_stock — импорт остатков из CSV 1С (шаг FSM ждёт документ)
# ------------------------------------------------------------
@router.message(Command("load_stock"))
async def cmd_load_stock(message: types.Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.set_state(AdminState.waiting_stock_file)
    await message.answer(
        "📥 <b>Загрузка остатков</b> — пришлите CSV 1С документом\n"
        "(cp1251 или utf-8, разделитель «;», колонки «Номенклатура;Ед;Кол-во»). "
        "Категории и параметры считаются автоматически, нераспознанное потом "
        "размечается: /classify_pending + /add_alias.\n\n/cancel — отмена.",
        parse_mode="HTML",
    )


@router.message(AdminState.waiting_stock_file, F.document)
async def stock_file_received(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """CSV-документ: байты → временный файл → import_stock_csv → отчёт.

    Байты — message.download(destination=io.BytesIO) (aiogram 3); файл
    создаётся tempfile.NamedTemporaryFile(delete=False) и удаляется в
    finally. Снапшоты берутся ПОСЛЕ импорта: list_snapshots(limit=2)
    (свежий первым), изменения — diff_snapshots(old_id, new_id).
    """
    if not _is_admin(message):
        return
    document = message.document
    filename = str(getattr(document, "file_name", "") or "stock.csv")
    if not filename.lower().endswith(".csv"):
        await state.clear()
        await message.answer(
            f"⚠️ «{_esc(filename)}» — не CSV. Нужен файл с расширением .csv и "
            "колонками «Номенклатура;Ед;Кол-во». /load_stock — заново.",
            parse_mode="HTML",
        )
        return
    buf = io.BytesIO()
    await bot.download(message.document, destination=buf)
    await state.clear()                        # файл принят — FSM больше не нужна
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, prefix="fpv_stock_", suffix=".csv"
        ) as tmp:
            tmp.write(buf.getvalue())
            tmp_path = Path(tmp.name)
        stats = await import_stock_csv(tmp_path)
    except Exception as e:                     # noqa: BLE001 — причину отдаём админу
        await message.answer(
            f"⚠️ Импорт не выполнен: {_esc(type(e).__name__)}: {_esc(str(e))}\n"
            "Файл не применён. Проверьте формат и пришлите заново: /load_stock.",
            parse_mode="HTML",
        )
        return
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
    # Перевысид рецептов (Этап 10): дозаполнение part_key слотов, сидированных
    # до появления позиций в parts (идемпотентно, правки владельца не трогает).
    try:
        async with get_sessionmaker()() as session:
            recipes_report = await seed_recipes(session)
        if recipes_report["renamed"]:
            system_logger.info(
                "Recipes titles renamed in place (Этап 10б): %s",
                recipes_report["renamed"],
            )
        if recipes_report["unresolved"]:
            system_logger.warning(
                "Recipes unresolved slots after stock import: %s",
                recipes_report["unresolved"],
            )
    except Exception as e:                     # noqa: BLE001 — импорт остатков важнее
        system_logger.warning(f"Recipes reseed after stock import failed: {e}")
    snaps = await list_snapshots(limit=2)      # снапшоты ПОСЛЕ импорта
    new_id = int(snaps[0]["id"]) if snaps else 0
    old_id = int(snaps[1]["id"]) if len(snaps) > 1 else None
    diff = await diff_snapshots(old_id, new_id) if old_id else None
    changes = diff.get("changed", []) if diff else []
    dialog_logger.info(json.dumps({
        "event": "admin_load_stock",
        "user_id": message.from_user.id,
        "file": filename,
        "total": stats.total,
        "snapshot_id": new_id,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False))
    report = (
        f"✅ Остатки загружены: «{_esc(filename)}»\n"
        f"Позиций: {stats.total}, новых: {stats.added}, "
        f"обнулено: {stats.removed}, пропущено: {stats.skipped}\n"
        f"Выгрузка #{new_id}" + (f" (пред. #{old_id})" if old_id else " (первая)")
    )
    if changes:
        report += (
            f"\n\nИзменения остатков: {len(changes)} "
            f"{_plural(len(changes), 'позиция', 'позиции', 'позиций')}\n"
            + "\n".join(_top_changes(changes))
        )
    other_count = (await count_by_category()).get("other", 0)
    if other_count:
        report += f"\n\n⚠️ Без категории: {other_count} → /classify_pending"
    await message.answer(report, parse_mode="HTML")


@router.message(AdminState.waiting_stock_file)
async def stock_not_document(message: types.Message, state: FSMContext) -> None:
    """Не-документ в шаге /load_stock: «отмена» — выход, остальное — подсказка."""
    raw = (message.text or "").strip()
    if _is_exit(raw):
        await _cancel(message, state)
        return
    await message.answer("Жду CSV-файл документом (или /cancel — отмена).")


# ------------------------------------------------------------
# /classify_pending — нераспознанные позиции (parts.category="other")
# ------------------------------------------------------------
@router.message(Command("classify_pending"))
async def cmd_classify_pending(message: types.Message) -> None:
    if not _is_admin(message):
        return
    pending = await list_parts(category="other")
    if not pending:
        await message.answer(
            "✅ Нераспознанных позиций нет — все части классифицированы."
        )
        return
    lines = [
        f"  • {_esc(p['name'])} — остаток {_fmt_qty(p['quantity'])}"
        for p in pending[:BUILDER_OTHER_LIMIT]
    ]
    tail = (
        f"\n… и ещё {len(pending) - BUILDER_OTHER_LIMIT}"
        if len(pending) > BUILDER_OTHER_LIMIT else ""
    )
    await message.answer(
        f"🧾 Нераспознанные позиции ({len(pending)}):\n"
        + "\n".join(lines) + tail
        + "\n\nРазметка: /add_alias <категория по-русски> <название>\n"
        "Категории: " + ", ".join(CATEGORY_LABELS.values())
        + "\nСписок алиасов: /aliases.",
        parse_mode="HTML",
    )


# ------------------------------------------------------------
# /add_alias — ручная разметка: upsert алиаса + пересчёт категорий
# ------------------------------------------------------------
@router.message(Command("add_alias"))
async def cmd_add_alias(message: types.Message) -> None:
    """/add_alias <категория> <название> (первое слово — категория).

    Upsert в part_aliases (add_alias) + reclassify_all_parts (+commit):
    алиасы-override побеждают автоклассификацию сразу, без переимпорта CSV.
    Если детали с таким именем в parts нет — алиас сохранится без part_key
    и применится при следующем /load_stock.
    """
    if not _is_admin(message):
        return
    tokens = (message.text or "").split(maxsplit=2)
    if len(tokens) < 3 or not tokens[2].strip():
        await message.answer(
            "Формат: /add_alias <категория по-русски> <название>\n"
            "Пример: /add_alias расходники Термоусадочная плёнка ПВХ\n"
            "Категории: " + ", ".join(CATEGORY_LABELS.values()),
        )
        return
    category = _category_key(tokens[1])
    if category is None:
        await message.answer(
            f"⚠️ Неизвестная категория «{_esc(tokens[1])}». Допустимые:\n"
            + ", ".join(CATEGORY_LABELS.values()),
            parse_mode="HTML",
        )
        return
    name = tokens[2].strip()
    alias = await add_alias(name, category_override=category, created_by=ADMIN_ID)
    async with get_sessionmaker()() as session:
        report = await reclassify_all_parts(session)
        await session.commit()
    dialog_logger.info(json.dumps({
        "event": "admin_add_alias",
        "user_id": message.from_user.id,
        "raw_name": alias["raw_name"],
        "category": category,
        "changed": report["changed"],
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False))
    tail = "" if alias["part_key"] else (
        "\n⚠️ Позиции с таким именем в справочнике пока нет — "
        "алиас применится при следующем /load_stock."
    )
    shown = CATEGORY_LABELS.get(category, category)
    await message.answer(
        f"✅ Алиас «{_esc(alias['raw_name'])}» → {shown} (#{alias['id']}).\n"
        f"Пересчёт выполнен, изменено позиций: {report['changed']}.{tail}",
        parse_mode="HTML",
    )


# ------------------------------------------------------------
# /aliases и /del_alias — список алиасов / удаление по id
# ------------------------------------------------------------
@router.message(Command("aliases"))
async def cmd_aliases(message: types.Message) -> None:
    if not _is_admin(message):
        return
    aliases = await list_aliases()
    if not aliases:
        await message.answer(
            "Алиасов нет. Создание: /add_alias <категория по-русски> <название>."
        )
        return
    lines = []
    for a in aliases:
        override = a.get("category_override")
        target = CATEGORY_LABELS.get(override, override) if override else a.get("part_key") or "—"
        lines.append(f"#{a['id']} {_esc(a['raw_name'])} → {_esc(str(target))}")
    await message.answer(
        f"🏷 Алиасы ({len(aliases)}):\n" + "\n".join(lines)
        + "\n\nУдаление: /del_alias <id>.",
        parse_mode="HTML",
    )


@router.message(Command("del_alias"))
async def cmd_del_alias(message: types.Message) -> None:
    if not _is_admin(message):
        return
    arg = (message.text or "").split(maxsplit=1)
    token = arg[1].strip() if len(arg) > 1 else ""
    if not token.isdigit():
        await message.answer("Формат: /del_alias <id> (id — из /aliases).")
        return
    if await delete_alias(int(token)):
        await message.answer(f"🗑 Алиас #{token} удалён. Пересчитать: /reclassify.")
    else:
        await message.answer(f"⚠️ Алиас #{token} не найден.")


# ------------------------------------------------------------
# /compat_rules — правила совместимости: список + toggle/add (Этап 8)
# ------------------------------------------------------------
async def _compat_rules_screen() -> tuple[str, types.InlineKeyboardMarkup]:
    """Текст + клавиатура экрана правил (общие для команды и toggle-callback)."""
    rules = await list_compat_rules(active_only=False)
    if not rules:
        head = "⚙️ Правил совместимости пока нет."
    else:
        rows = [
            f"{'✅' if r['is_active'] else '⛔'} #{r['id']} "
            f"<b>{_esc(r['rule_type'])}</b> — {_esc(str(r['description'] or '—'))}"
            for r in rules
        ]
        head = f"⚙️ <b>Правила совместимости</b> ({len(rules)}):\n\n" + "\n".join(rows)
    return (
        head + "\n\n✅/⛔ — вкл/выкл кнопкой; «➕ Добавить правило» — создать.",
        kb_compat_rules(rules),
    )


@router.message(Command("compat_rules"))
async def cmd_compat_rules(message: types.Message) -> None:
    if not _is_admin(message):
        return
    text, kb = await _compat_rules_screen()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith(COMPAT_TOGGLE_PREFIX))
async def cb_compat_toggle(callback: types.CallbackQuery) -> None:
    """cr:toggle:<id> — вкл/выкл правила и перерисовка экрана (не-админ — тишина)."""
    if not _cb_is_admin(callback):
        return
    token = (callback.data or "")[len(COMPAT_TOGGLE_PREFIX):]
    if not token.isdigit():
        await safe_answer(callback, "Некорректный id правила", show_alert=True)
        return
    rules = await list_compat_rules(active_only=False)
    current = next((r for r in rules if r["id"] == int(token)), None)
    if current is None or not await set_compat_rule_active(
        int(token), not current["is_active"]
    ):
        await safe_answer(callback, f"Правило #{token} не найдено", show_alert=True)
        return
    text, kb = await _compat_rules_screen()
    await edit_or_send(callback.message, text, reply_markup=kb,
                       warn_prefix="admin compat_rules edit failed")
    await safe_answer(callback, "⛔ Отключено" if current["is_active"] else "✅ Включено")


@router.callback_query(F.data == "cr:add")
async def cb_compat_add(callback: types.CallbackQuery, state: FSMContext) -> None:
    """cr:add — FSM добавления правила: rule_type → params (JSON) → описание."""
    if not _cb_is_admin(callback):
        return
    await safe_answer(callback, )
    await state.set_state(AdminState.waiting_rule_type)
    await callback.message.answer(
        "➕ <b>Новое правило совместимости</b> — шаг 1/3.\n"
        "Введите rule_type (короткое имя, например <code>vtx_vrx_band</code>).\n\n"
        "/cancel — отмена.",
        parse_mode="HTML",
    )


@router.message(AdminState.waiting_rule_type, F.text)
async def process_rule_type(message: types.Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if _is_exit(raw):
        await _cancel(message, state)
        return
    if raw.startswith("/"):
        await _not_command(message)
        return
    if not raw:
        await message.answer("rule_type не может быть пустым. Введите строку ещё раз.")
        return
    await state.update_data(rule_type=raw)
    await state.set_state(AdminState.waiting_rule_params)
    await message.answer(
        f"Тип: <b>{_esc(raw)}</b> — шаг 2/3.\n"
        "Пришлите <b>params</b> как JSON-объект, например:\n"
        '<code>{"slots": ["vtx", "vrx"]}</code>\n\n'
        "/cancel — отмена.",
        parse_mode="HTML",
    )


@router.message(AdminState.waiting_rule_type)
async def rule_type_not_text(message: types.Message) -> None:
    await message.answer("Жду rule_type текстом — отправьте его сообщением.")


@router.message(AdminState.waiting_rule_params, F.text)
async def process_rule_params(message: types.Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if _is_exit(raw):
        await _cancel(message, state)
        return
    if raw.startswith("/"):
        await _not_command(message)
        return
    try:
        params = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        await message.answer(
            "Это не JSON. Нужен JSON-объект в фигурных скобках, пример:\n"
            '<code>{"slots": ["vtx", "vrx"]}</code>\n'
            "Попробуйте ещё раз."
        )
        return
    if not isinstance(params, dict):
        await message.answer(
            "JSON должен быть объектом (в фигурных скобках), пример:\n"
            '<code>{"slots": ["vtx", "vrx"]}</code>\n'
            "Попробуйте ещё раз."
        )
        return
    await state.update_data(params=params)
    await state.set_state(AdminState.waiting_rule_desc)
    await message.answer(
        f"Params: <code>{_esc(json.dumps(params, ensure_ascii=False))}</code>"
        " — шаг 3/3.\n"
        "Введите короткое описание правила (или «-» — без описания).",
        parse_mode="HTML",
    )


@router.message(AdminState.waiting_rule_params)
async def rule_params_not_text(message: types.Message) -> None:
    await message.answer("Жду params текстом — JSON-объектом, например "
                         '{"slots": ["vtx", "vrx"]}.')


@router.message(AdminState.waiting_rule_desc, F.text)
async def process_rule_desc(message: types.Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if _is_exit(raw):
        await _cancel(message, state)
        return
    if raw.startswith("/"):
        await _not_command(message)
        return
    description = None if raw in {"", "-"} else raw
    data = await state.get_data()
    rule = await add_compat_rule(
        data.get("rule_type", ""),
        params=data.get("params", {}),
        description=description,
    )
    await state.clear()
    dialog_logger.info(json.dumps({
        "event": "admin_add_compat_rule",
        "user_id": message.from_user.id,
        "rule_id": rule["id"],
        "rule_type": rule["rule_type"],
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False))
    await message.answer(
        f"✅ Правило #{rule['id']} ({_esc(rule['rule_type'])}) добавлено и включено.\n"
        "Экран правил: /compat_rules.",
        parse_mode="HTML",
    )


@router.message(AdminState.waiting_rule_desc)
async def rule_desc_not_text(message: types.Message) -> None:
    await message.answer("Жду описание текстом (или «-» — без описания).")


# ------------------------------------------------------------
# /stock_stats — расход остатков между двумя последними снапшотами
# ------------------------------------------------------------
@router.message(Command("stock_stats"))
async def cmd_stock_stats(message: types.Message) -> None:
    if not _is_admin(message):
        return
    snaps = await list_snapshots(limit=2)
    if len(snaps) < 2:
        await message.answer(
            "Нужно минимум две выгрузки остатков: загрузите два CSV через /load_stock."
        )
        return
    old_snap, new_snap = snaps[1], snaps[0]
    diff = await diff_snapshots(old_snap["id"], new_snap["id"])
    changed = diff.get("changed", [])
    decreases = [r for r in changed if float(r["new"]) < float(r["old"])]
    increases = [r for r in changed if float(r["new"]) > float(r["old"])]
    spent = sum(float(r["old"]) - float(r["new"]) for r in decreases)
    gained = sum(float(r["new"]) - float(r["old"]) for r in increases)
    await message.answer(
        f"📊 <b>Расход остатков</b> #{old_snap['id']} → #{new_snap['id']} "
        f"({_fmt_dt(old_snap['loaded_at'])} → {_fmt_dt(new_snap['loaded_at'])})\n\n"
        f"⬇️ Убывания: {len(decreases)} "
        f"{_plural(len(decreases), 'позиция', 'позиции', 'позиций')}"
        f", всего -{_fmt_qty(spent)}\n"
        f"⬆️ Пополнения: {len(increases)} "
        f"{_plural(len(increases), 'позиция', 'позиции', 'позиций')}"
        f", всего +{_fmt_qty(gained)}\n"
        f"➕ Новые позиции: {len(diff.get('added', []))}, "
        f"исчезли: {len(diff.get('removed', []))}"
        + (("\n\n<b>Топ изменений:</b>\n" + "\n".join(_top_changes(changed)))
           if changed else ""),
        parse_mode="HTML",
    )


# ------------------------------------------------------------
# /reclassify — пересчёт category/params всех позиций parts
# ------------------------------------------------------------
@router.message(Command("reclassify"))
async def cmd_reclassify(message: types.Message) -> None:
    if not _is_admin(message):
        return
    async with get_sessionmaker()() as session:
        report = await reclassify_all_parts(session)
        await session.commit()
    other = report.get("other", [])
    tail = ""
    if other:
        lines = [
            f"  • {_esc(p['name'])}"
            for p in other[:BUILDER_OTHER_LIMIT]
        ]
        more = (
            f"\n… и ещё {len(other) - BUILDER_OTHER_LIMIT}"
            if len(other) > BUILDER_OTHER_LIMIT else ""
        )
        tail = "\n⚠️ Без категории: " + str(len(other)) + "\n" + "\n".join(lines) + more
    await message.answer(
        f"♻️ Пересчёт позиций: {report['total']}, изменено: {report['changed']}.{tail}",
        parse_mode="HTML",
    )


# ------------------------------------------------------------
# /recipes — серийные конфигурации: собираемость по остаткам (Этап 10)
# ------------------------------------------------------------
@router.message(Command("recipes"))
async def cmd_recipes(message: types.Message) -> None:
    """📦 Рецепты RECIPE_DICT: «{title} — можно собрать: N шт» (L4 по остаткам).

    Собираемость — recipe_builds (min по слотам floor(остаток/qty)); слот с
    part_key=None (имя из 1С не нашлось в каталоге на момент сида) обнуляет
    сборку и выводится ⚠️-списком с подсказкой про /add_alias.
    """
    if not _is_admin(message):
        return
    stock = {p["key"]: int(p.get("quantity") or 0) for p in await list_parts()}
    lines: list[str] = []
    unresolved_total = 0
    for recipe in await list_recipes(active_only=True):
        unresolved = [
            s for s in recipe.get("slots") or [] if not s.get("part_key")
        ]
        unresolved_total += len(unresolved)
        builds = recipe_builds(
            recipe.get("slots") or [], stock, str(recipe.get("title") or "")
        )
        row = (
            f"{'⚠️' if unresolved else '✅'} {_esc(recipe['title'])} · "
            f"{int(recipe['size'])}″ — можно собрать: {builds} шт"
        )
        if unresolved:
            names = ", ".join(_esc(s.get("name") or "?") for s in unresolved)
            row += f"\n      нет в каталоге: {names}"
        lines.append(row)
    header = f"📦 <b>Серийные конфигурации ({len(lines)})</b>"
    if unresolved_total:
        header += (
            f"\n⚠️ Слотов вне каталога: {unresolved_total} — "
            "добавьте алиасы (/add_alias) или поправьте data/builder/recipes.py."
        )
    await message.answer(
        header + ("\n" + "\n".join(lines) if lines else ""),
        parse_mode="HTML",
    )

