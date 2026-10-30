"""Клавиатуры «🛠 Конструктора дронов» (Этап 6).

Только aiogram-типы и справочник слотов: без логики подбора (services/
builder/matcher.py), проверок совместимости (services/builder/compat.py) и
обращения к БД. Каждый билдер возвращает НОВЫЙ InlineKeyboardMarkup (никаких
глобальных мутабельных клавиатур — стиль keyboards/keyboards.py).

Callback-data (контракт Этапов 6/10, схема id — Этап 9а; обрабатываются
handlers/builder.py; здесь только раскладка):
    b:mode:recipe | b:mode:free | b:start:resume | b:start:drafts
                                                    — меню конструктора;
    b:size:<n>                                      — размер борта (Этап 10);
    b:motors:<n>                                    — число моторов свободной
        сборки (Этап 11в: 15″ — 4 или 6);
    b:mto_frame                                     — рама «под заказ» в слот
        frame (Этап 11в: 6/8-моторные сборки, складских рам нет);
    b:recipe:<id>                                   — карточка рецепта;
    b:recipe:build:<id>                             — рецепт → предзаполненная
        сборка (слот рамы остаётся пустым — рама выбирается по размеру);
    b:recipe:back                                   — из карточки к списку
        рецептов размера;
    b:slot:<category>                               — выбор слота;
    b:part:<id>                                     — выбор детали в слот
        (id — int из parts.id, 1–4 байта; НЕ ключ-имя);
    b:page:+1 | b:page:-1                           — пагинация part_list;
    b:sugg:<category>:<id>                          — подсказка замены на
        экране проверки (Этап 11): деталь id → в слот category;
    b:input | b:back | b:review | b:save | b:done | b:cancel;
    b:draft:load:<id> | b:draft:del:<id>            — черновики.
    cr:toggle:<id> | cr:add                         — админ-экран правил
        совместимости (Этап 8, обработка в handlers/admin.py).

ЗАПРЕТ (Этап 9а, боевой краш BUTTON_DATA_INVALID): НИКОГДА не класть в
callback_data имена, ключи-имена, описания, JSON — только id и короткие
токены. Лимит Telegram — 64 байта UTF-8 на callback_data ОДНОЙ кнопки;
при превышении отклоняется ВСЁ сообщение с клавиатурой (TelegramBadRequest:
BUTTON_DATA_INVALID). Длинные строки — только в текстах кнопок (текст
лимитируется слабее). Guard-тест: tests/test_kb_callback_limits.py.

Индикатор страницы «1/3» некликабельный — callback_data "dummy" (глобальная
no-op заглушка handlers/menu.py, как «Нет совпадений» в keyboards/keyboards.py).

Label'ы человекочитаемые: имя детали усекается до MAX_LABEL_LEN (40) символов
с «…» (trunc_label). Порядок кнопок детерминирован: порядок BUILD_SLOTS,
сортировка кандидатов/черновиков — на стороне вызывающего (matcher/фасад).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from data.builder.categories import BUILD_SLOTS
from services.builder.labels import fmt_qty, label

__all__ = [
    "MAX_LABEL_LEN",
    "PAGE_SIZE",
    "kb_compat_rules",
    "kb_drafts",
    "kb_frame_mto",
    "kb_motor_count",
    "kb_part_list",
    "kb_recipe",
    "kb_recipe_list",
    "kb_review",
    "kb_size_menu",
    "kb_slot_menu",
    "kb_start",
    "trunc_label",
]

MAX_LABEL_LEN = 40  # предел имени детали в label кнопки (дальше — «…»)
PAGE_SIZE = 10      # строк списка деталей на страницу (пагинация part_list)


def trunc_label(name: str, limit: int = MAX_LABEL_LEN) -> str:
    """Имя детали → label кнопки: ≤ limit символов, длинное — с «…»."""
    name = str(name or "")
    if len(name) <= limit:
        return name
    return name[: limit - 1] + "…"


def kb_start() -> InlineKeyboardMarkup:
    """Стартовое меню конструктора (Этап 10): два режима + черновики.

    [📦 Серийные конфигурации] — сборка строго по производственным рецептам;
    [🛠 Свободная сборка] — прежний поток слотов, но с фильтрацией по модели
    совместимости (L1-whitelist для категорий "recipe").
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Серийные конфигурации", callback_data="b:mode:recipe")],
            [InlineKeyboardButton(text="🛠 Свободная сборка", callback_data="b:mode:free")],
            [InlineKeyboardButton(text="📂 Продолжить черновик", callback_data="b:start:resume")],
            [InlineKeyboardButton(text="🗂 Мои черновики", callback_data="b:start:drafts")],
        ]
    )


def kb_size_menu(sizes: list[int | str]) -> InlineKeyboardMarkup:
    """Выбор размера борта + отдельная кнопка Hex (гексакоптер — тип рамы).

    sizes — размеры (int) и/или "hex"; для "hex" кнопка «Hex» с
    callback b:size:hex (все гекса-сборки — только через Hex).
    """
    rows: list[list[InlineKeyboardButton]] = []
    for size in sizes:
        if size == "hex":
            rows.append([InlineKeyboardButton(text="Hex", callback_data="b:size:hex")])
        else:
            rows.append([InlineKeyboardButton(text=f"{size}″", callback_data=f"b:size:{size}")])
    rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data="b:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_recipe_list(recipes: list[dict]) -> InlineKeyboardMarkup:
    """Список рецептов размера: «{title} — N шт» + [⬅ Назад].

    recipes — уже с посчитанным builds (ключ "builds"); callback — только id
    (лимит 64 байта, Этап 9а).
    """
    rows = [
        [
            InlineKeyboardButton(
                text=f"{trunc_label(r['title'])} — {fmt_qty(r.get('builds', 0))}",
                callback_data=f"b:recipe:{r['id']}",
            )
        ]
        for r in recipes
    ]
    rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data="b:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _motor_word(n: int) -> str:
    """«4 мотора», «6/8 моторов» — русская форма множественного числа."""
    return "мотора" if n % 10 == 4 else "моторов"


def kb_motor_count(counts: list[int]) -> InlineKeyboardMarkup:
    """Выбор числа моторов (Этап 11в, свободный режим): [4 мотора, 6 моторов, ⬅ Назад].

    Показывается после выбора размера, если по размеру есть серийные сборки
    с разным числом моторов (сейчас 15″: квадро ×4 и гекса-рецепт ×6) —
    whitelist слотов (L1) и наличие рам зависят от пары (размер, моторы).
    """
    rows = [
        [InlineKeyboardButton(text=f"{n} {_motor_word(n)}", callback_data=f"b:motors:{n}")]
        for n in sorted({int(c) for c in counts or ()})
    ]
    rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data="b:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_frame_mto() -> InlineKeyboardMarkup:
    """Слот рамы при 6/8 моторах (Этап 11в): [📭 Рама под заказ, ⬅ Назад]."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📭 Рама под заказ", callback_data="b:mto_frame")],
            [InlineKeyboardButton(text="⬅ Назад", callback_data="b:back")],
        ]
    )


def kb_recipe(recipe_id: int) -> InlineKeyboardMarkup:
    """Карточка рецепта: [🛠 Оформить сборку, ⬅ Назад].

    «Оформить сборку» (b:recipe:build:<id>) предзаполняет слоты рецепта
    (рама остаётся пустой — выбирается по размеру на шаге оформления);
    «Назад» (b:recipe:back) — к списку рецептов размера.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛠 Оформить сборку", callback_data=f"b:recipe:build:{recipe_id}")],
            [InlineKeyboardButton(text="⬅ Назад", callback_data="b:recipe:back")],
        ]
    )


def kb_slot_menu(slots: dict, build_size=None) -> InlineKeyboardMarkup:
    """Меню слотов: 7 кнопок BUILD_SLOTS (✅/⬜ + подпись категории) +
    [📋 Проверить сборку, 💾 Сохранить черновик, ❌ Выйти] — итого 10
    (battery/tx — хотфикс Этапа 10а, антенны/VRX/payload — хотфикс Этапа
    10б — выведены из слотов борта).

    build_size в разметке не участвует (набор слотов от размера не зависит) —
    параметр оставлен контрактом хендлера для будущих экранов.
    """
    slots = slots or {}
    frame_mto = bool((slots.get("frame") or {}).get("made_to_order"))
    rows = []
    for category, _required, _title in BUILD_SLOTS:
        # Раздельная пара fc+esc закрывает слот стека (рецепты 13″/15″, Этап 10в).
        # Рама «под заказ» (mto-слот, Этап 11в) считается выбранной — ✅.
        covered = category in slots or (
            category == "fc_esc_stack" and slots.get("fc") and slots.get("esc")
        ) or (category == "frame" and frame_mto)
        mark = "✅" if covered else "⬜"
        rows.append([InlineKeyboardButton(text=f"{mark} {label(category)}", callback_data=f"b:slot:{category}")])
    rows.append(
        [
            InlineKeyboardButton(text="📋 Проверить сборку", callback_data="b:review"),
            InlineKeyboardButton(text="💾 Сохранить черновик", callback_data="b:save"),
            InlineKeyboardButton(text="❌ Выйти", callback_data="b:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_part_list(
    page_parts: list, page: int, pages: int, slot: str, serial_hint: bool = False
) -> InlineKeyboardMarkup:
    """Список деталей слота: до PAGE_SIZE кнопок + пагинация + [🔍 Поиск, ⬅ Назад].

    serial_hint=True (Этап 10: union серийных сборок для узла пуст) добавляет
    кнопку «📦 Серийные конфигурации» (b:mode:recipe) — прямой переход в
    серийный режим.

    page_parts — уже отрезанная страница (пагинация в хендлере); page — 0-based;
    label: «<имя (≤40 с «…»)> · <кол-во>», callback — b:part:<id> (int
    parts.id: ключи-имена длинных деталей превышали лимит Telegram 64 байта
    на callback_data — Этап 9а; в state/черновиках остаются ключи).
    """
    page_parts = page_parts or []
    rows = []
    for part_dict in page_parts[:PAGE_SIZE]:
        button_text = (
            f"{part_dict.get('name')} · "
            f"{fmt_qty(part_dict.get('quantity'), part_dict.get('unit') or 'шт')}"
        )
        rows.append([InlineKeyboardButton(text=button_text, callback_data=f"b:part:{part_dict['id']}")])
    page = max(0, int(page))
    pages = max(1, int(pages))
    rows.append(
        [
            InlineKeyboardButton(text="‹ Пред.", callback_data="b:page:-1"),
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="dummy"),
            InlineKeyboardButton(text="След. ›", callback_data="b:page:+1"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="🔍 Поиск по названию", callback_data="b:input"),
            InlineKeyboardButton(text="⬅ Назад", callback_data="b:back"),
        ]
    )
    if serial_hint:
        rows.append(
            [InlineKeyboardButton(text="📦 Серийные конфигурации", callback_data="b:mode:recipe")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_review(result: dict | None = None) -> InlineKeyboardMarkup:
    """Итог проверки: [🔧 Изменить состав, 💾 Сохранить черновик, ✅ Готово].

    result — dict из check_compatibility (Этап 5); набор кнопок фиксирован
    контрактом Этапа 6 (изменять состав можно и у совместимой сборки).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔧 Изменить состав", callback_data="b:back")],
            [InlineKeyboardButton(text="💾 Сохранить черновик", callback_data="b:save")],
            [InlineKeyboardButton(text="✅ Готово", callback_data="b:done")],
        ]
    )


def kb_drafts(drafts: list) -> InlineKeyboardMarkup:
    """Список черновиков: на каждый — [📂 Открыть: имя, 🗑 Удалить], затем ⬅ Назад."""
    rows = []
    for draft in drafts or ():
        draft_id = int(draft.get("id") or 0)
        name = trunc_label(draft.get("name") or f"Черновик {draft_id}")
        rows.append(
            [
                InlineKeyboardButton(text=f"📂 Открыть: {name}", callback_data=f"b:draft:load:{draft_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"b:draft:del:{draft_id}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data="b:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_compat_rules(rules: list) -> InlineKeyboardMarkup:
    """Экран правил совместимости (админ-контур): [✅/⛔ описание правила] + [➕].

    rules — RULE_DICT из db.repositories_builder (id, rule_type, is_active,
    description). Тумблер показывает русское описание правила; без описания —
    «Правило #<id>». Callback'и cr:toggle:<id> / cr:add обрабатывает
    handlers/admin.py — в этом модуле только раскладка (как для «b:*»).
    """
    rows = []
    for rule in rules or ():
        rule_id = int(rule.get("id") or 0)
        mark = "✅" if rule.get("is_active") else "⛔"
        description = str(rule.get("description") or "").strip()
        text = trunc_label(description or f"Правило #{rule_id}")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {text}",
                    callback_data=f"cr:toggle:{rule_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить правило", callback_data="cr:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

