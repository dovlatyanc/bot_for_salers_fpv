"""Клавиатуры «Конструктора» (Этап 6): раскладка и callback-data.

Раскладки билдеров keyboards/builder.py: старт, 7 слотов, список деталей
(пагинация 10/стр, усечение имён до 40 символов с «…»), итог, черновики.
Callback-data сверяются со «снапшотами» — полная проверка покрытия префиксов
хендлерами в tests/test_builder_handlers.py (через handler.check()).
"""

from data.builder.categories import BUILD_SLOTS
from keyboards.builder import (
    MAX_LABEL_LEN,
    kb_drafts,
    kb_part_list,
    kb_recipe,
    kb_recipe_list,
    kb_review,
    kb_size_menu,
    kb_slot_menu,
    kb_start,
    trunc_label,
)
from services.builder.labels import label
from tests.fakes import kb_data


def part(part_id: int, name: str, qty=1.0):
    """Достаточный для кнопки фрагмент PART_DICT: id/name/quantity."""
    return {"id": part_id, "name": name, "quantity": qty}


# ---------- kb_start ----------

def test_kb_start():
    rows = kb_data(kb_start())
    assert [cb for _, cb in rows] == [
        "b:mode:recipe",
        "b:mode:free",
        "b:start:resume",
        "b:start:drafts",
    ]
    assert all(text for text, _ in rows)


# ---------- kb_size_menu / kb_recipe_list / kb_recipe (Этап 10) ----------

def test_kb_size_menu():
    rows = kb_data(kb_size_menu([10, 13, 15, "hex"]))
    assert [cb for _, cb in rows] == [
        "b:size:10", "b:size:13", "b:size:15", "b:size:hex", "b:cancel",
    ]
    assert rows[0][0] == "10″"
    assert rows[3][0] == "Hex"       # гексакоптер — тип рамы, кнопка «Hex»


def test_kb_recipe_list():
    recipes = [
        {"id": 1, "title": "MARK4 10″", "builds": 3},
        {"id": 2, "title": "Очень длинное название рецепта серийной сборки 15 дюймов", "builds": 0},
    ]
    rows = kb_data(kb_recipe_list(recipes))
    assert rows[0] == ("MARK4 10″ — 3 шт", "b:recipe:1")
    assert rows[1][1] == "b:recipe:2"
    assert len(rows[1][0]) <= 40 + len(" — 0 шт")  # имя усечено trunc_label
    assert rows[-1] == ("⬅ Назад", "b:back")


def test_kb_recipe():
    rows = kb_data(kb_recipe(7))
    assert rows == [
        ("🛠 Оформить сборку", "b:recipe:build:7"),
        ("⬅ Назад", "b:recipe:back"),
    ]


# ---------- kb_slot_menu ----------

def test_kb_slot_menu_structure():
    rows = kb_data(kb_slot_menu({}, None))
    assert len(rows) == len(BUILD_SLOTS) + 3  # 7 слотов + [📋 Итог, 💾, ❌]
    assert [cb for _, cb in rows[: len(BUILD_SLOTS)]] == [
        f"b:slot:{category}" for category, _required, _title in BUILD_SLOTS
    ]
    assert [text for text, _ in rows[: len(BUILD_SLOTS)]] == [
        f"⬜ {label(category)}" for category, _required, _title in BUILD_SLOTS
    ]
    assert rows[-1] == ("❌ Выйти", "b:cancel")
    assert ("📋 Проверить сборку", "b:review") in rows
    assert ("💾 Сохранить черновик", "b:save") in rows


def test_kb_slot_menu_filled_marks():
    slots = {"frame": {"key": "k", "qty": 1}, "vtx": {"key": "v", "qty": 1}}
    rows = kb_data(kb_slot_menu(slots, 10))
    texts = [text for text, _ in rows[: len(BUILD_SLOTS)]]
    assert f"✅ {label('frame')}" in texts
    assert f"✅ {label('vtx')}" in texts
    assert f"⬜ {label('motor')}" in texts


# ---------- kb_part_list ----------

def test_kb_part_list_first_page():
    parts = [part(i, f"Деталь {i}") for i in range(12)]
    rows = kb_data(kb_part_list(parts[:10], 0, 2, "frame"))
    assert len(rows) == 10 + 3 + 2  # детали + пагинация + [🔍 Поиск, ⬅ Назад]
    assert [cb for _, cb in rows[:10]] == [f"b:part:{i}" for i in range(10)]
    assert rows[10:13] == [("‹ Пред.", "b:page:-1"), ("1/2", "dummy"), ("След. ›", "b:page:+1")]
    assert rows[13:15] == [("🔍 Поиск по названию", "b:input"), ("⬅ Назад", "b:back")]


def test_kb_part_list_second_page():
    parts = [part(i, f"Деталь {i}") for i in range(12)]
    rows = kb_data(kb_part_list(parts[10:], 1, 2, "frame"))
    assert len(rows) == 2 + 3 + 2
    assert [cb for _, cb in rows[:2]] == ["b:part:10", "b:part:11"]
    assert rows[2:5] == [("‹ Пред.", "b:page:-1"), ("2/2", "dummy"), ("След. ›", "b:page:+1")]


def test_kb_part_list_single_page():
    parts = [part(0, "Деталь 0")]
    rows = kb_data(kb_part_list(parts, 0, 1, "frame"))
    assert rows[1:4] == [("‹ Пред.", "b:page:-1"), ("1/1", "dummy"), ("След. ›", "b:page:+1")]


def test_kb_part_list_truncates_label():
    long_name = "Очень длинное название детали, которое заведомо не влезает в кнопку телеграма целиком"
    rows = kb_data(kb_part_list([part(7, long_name)], 0, 1, "frame"))
    text, callback = rows[0]
    assert callback == "b:part:7"
    # имя усечено до MAX_LABEL_LEN, к кнопке добавлено « · 1 шт» (fmt_qty, Этап 9)
    assert text == long_name[: MAX_LABEL_LEN - 1] + "…" + " · 1 шт"


def test_kb_part_list_serial_hint_button():
    """serial_hint=True (union "recipe"-узла пуст) → кнопка серийного режима."""
    rows = kb_data(kb_part_list([], 0, 1, "fc_esc_stack", serial_hint=True))
    assert ("📦 Серийные конфигурации", "b:mode:recipe") in rows
    rows_plain = kb_data(kb_part_list([], 0, 1, "frame"))
    assert ("📦 Серийные конфигурации", "b:mode:recipe") not in rows_plain


def test_kb_part_list_qty_formatting():
    rows = kb_data(kb_part_list([part(1, "Деталь", qty=2.5)], 0, 1, "frame"))
    assert rows[0] == ("Деталь · 2,5 шт", "b:part:1")
    rows = kb_data(kb_part_list([part(1, "Деталь", qty=1.0)], 0, 1, "frame"))
    assert rows[0] == ("Деталь · 1 шт", "b:part:1")


# ---------- kb_review ----------

def test_kb_review():
    result = {"ok": False, "missing": ["vtx", "camera"], "errors": [], "warnings": []}
    rows = kb_data(kb_review(result))
    assert [cb for _, cb in rows] == ["b:back", "b:save", "b:done"]


# ---------- kb_drafts ----------

def test_kb_drafts_two_buttons_per_draft():
    drafts = [{"id": 1, "name": "Черновик 09.10.2026"}, {"id": 2, "name": "Черновик 08.10.2026"}]
    rows = kb_data(kb_drafts(drafts))
    assert len(rows) == 2 * 2 + 1  # 2 кнопки на черновик + ⬅ Назад
    assert rows[-1] == ("⬅ Назад", "b:back")
    assert [cb for _, cb in rows[:-1]] == [
        "b:draft:load:1",
        "b:draft:del:1",
        "b:draft:load:2",
        "b:draft:del:2",
    ]


def test_kb_drafts_empty():
    rows = kb_data(kb_drafts([]))
    assert rows == [("⬅ Назад", "b:back")]


# ---------- trunc_label ----------

def test_trunc_label_short_name_untouched():
    assert trunc_label("короткое имя") == "короткое имя"


def test_trunc_label_long_name_adds_ellipsis():
    assert trunc_label("x" * 50) == "x" * (MAX_LABEL_LEN - 1) + "…"
    assert len(trunc_label("y" * 41)) == MAX_LABEL_LEN
