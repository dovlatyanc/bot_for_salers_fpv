"""Guard-тест лимита callback_data (Этап 9а): TelegramBadRequest: BUTTON_DATA_INVALID.

Боевой краш: Telegram отклоняет ВСЁ сообщение с клавиатурой, если callback_data
ХОТЯ БЫ одной inline-кнопки длиннее 64 байт (UTF-8). До Этапа 9а в b:part:<…>
клали нормализованный ключ-имя детали, и длинные имена (например «Полетный
контроллер + регулятор оборотов GEPRC TAKER F722 BLS 80A V2» — ключ ~140 байт)
роняли answer() клавиатуры part_list на живой БД (143 позиции).

Контракт keyboards/builder.py: в callback_data — только id и короткие токены;
имена/описания — ТОЛЬКО в текстах кнопок (текст лимитируется слабее). Здесь
прогоняются ХУДШИЕ входы всех билдеров keyboards/builder.py. Лимит на текст
кнопки не проверяется — он не является причиной BUTTON_DATA_INVALID.
"""

from data.builder.categories import BUILD_SLOTS
from data.builder.keywords import REAL_EXAMPLES
from keyboards.builder import (
    kb_compat_rules,
    kb_drafts,
    kb_part_list,
    kb_review,
    kb_slot_menu,
    kb_start,
)
from tests.fakes import kb_data

CB_LIMIT = 64  # байт UTF-8 на callback_data одной кнопки (лимит Telegram)


def check_kb(kb) -> list[tuple[str, str]]:
    """Для ВСЕХ inline-кнопок kb: callback_data — str и 1..64 байт UTF-8.

    Возвращает [(text, callback_data)] — тест может дополнительно сверить
    состав. Нарушение лимита хоть одной кнопкой отклоняет всё сообщение,
    поэтому проверяется каждая кнопка каждой клавиатуры.
    """
    buttons = kb_data(kb)
    for _text, data in buttons:
        assert isinstance(data, str), f"callback_data не строка: {data!r}"
        size = len(data.encode("utf-8"))
        assert size >= 1, "пустая callback_data"
        assert size <= CB_LIMIT, (
            f"callback_data {data!r} = {size} байт > лимита {CB_LIMIT} "
            "(TelegramBadRequest: BUTTON_DATA_INVALID)"
        )
    return buttons


def part(part_id: int, name: str, qty: float = 1.0) -> dict:
    """Фрагмент PART_DICT, достаточный для кнопки: id/name/quantity (+key)."""
    return {"id": part_id, "key": name.lower(), "name": name, "quantity": qty}


def _longest_real_names(n: int) -> list[str]:
    """n САМЫХ длинных имён из REAL_EXAMPLES (боевой дамп 1С)."""
    all_names = [name for names in REAL_EXAMPLES.values() for name in names]
    return sorted(all_names, key=len, reverse=True)[:n]


# ============================================================
# kb_part_list: худшие входы
# ============================================================

def test_part_list_synthetic_250_char_names():
    """Синтетика: 10 деталей с именами ровно 250 символов — кнопки в лимите."""
    names = [f"Деталь {i} " + "Х" * (250 - len(f"Деталь {i} ")) for i in range(1, 11)]
    assert min(map(len, names)) == 250
    parts = [part(i, name) for i, name in enumerate(names, start=1)]
    rows = check_kb(kb_part_list(parts, 0, 1, "fc_esc_stack"))
    assert [cb for _, cb in rows[:10]] == [f"b:part:{i}" for i in range(1, 11)]


def test_part_list_real_examples_longest_names():
    """САМЫЕ длинные боевые имена REAL_EXAMPLES — кнопки в лимите."""
    longest = _longest_real_names(10)
    parts = [part(i, name) for i, name in enumerate(longest, start=1)]
    rows = check_kb(kb_part_list(parts, 0, 1, "fc_esc_stack"))
    assert [cb for _, cb in rows[:10]] == [f"b:part:{i}" for i in range(1, 11)]
    # контроль осмысленности: худшее имя длиннее безопасных для ключа ~56 символов
    assert len(longest[0]) > 56


def test_part_list_production_long_literals():
    """Боевые длинные имена-литералы (виновники краша) — кнопки в лимите."""
    victims = (
        "Полетный контроллер + регулятор оборотов GEPRC TAKER F722 BLS 80A V2",
        "Термоусадочная пленка из ПВХ Ширина 120 мм ? 0,12 мм (1 кг ? 22 м)",
        "Кабель с разъемом 30см, JST SH 1.0мм 7pin / разъем на плату JST SH 7pin / 5шт",
    )
    parts = [part(i, name) for i, name in enumerate(victims, start=1)]
    rows = check_kb(kb_part_list(parts, 0, 1, "consumable"))
    assert [cb for _, cb in rows[:3]] == ["b:part:1", "b:part:2", "b:part:3"]


def test_part_list_huge_ids_stay_in_limit():
    """id на границе int32 (4 байта) — callback_data всё ещё короткий."""
    parts = [part(4294967295, "Х" * 250), part(1, "Обычная деталь")]
    rows = check_kb(kb_part_list(parts, 0, 1, "frame"))
    assert [cb for _, cb in rows[:2]] == ["b:part:4294967295", "b:part:1"]


# ============================================================
# kb_slot_menu / kb_drafts / kb_compat_rules / kb_start / kb_review
# ============================================================

def test_slot_menu_all_slots_with_long_chosen_names():
    """Все 7 слотов с длинными ключами выбранных деталей — b:slot:<category> в лимите.

    Длинные имена в ТЕКСТАХ кнопок допустимы (Telegram лимитирует текст слабее) —
    проверяется только callback_data.
    """
    longest = _longest_real_names(len(BUILD_SLOTS))
    slots = {
        category: {"key": name.lower(), "qty": 1}
        for (category, _required, _title), name in zip(BUILD_SLOTS, longest)
    }
    rows = check_kb(kb_slot_menu(slots, 10))
    assert len(rows) == len(BUILD_SLOTS) + 3


def test_drafts_ten_with_long_names():
    """10 черновиков с длинными именами — b:draft:load/del:<id> в лимите."""
    drafts = [{"id": i, "name": "Черновик " * 20 + str(i)} for i in range(1, 11)]
    rows = check_kb(kb_drafts(drafts))
    assert [cb for _, cb in rows[:-1]] == [
        cb for i in range(1, 11) for cb in (f"b:draft:load:{i}", f"b:draft:del:{i}")
    ]


def test_compat_rules_long_descriptions():
    """Правила с описаниями 300 символов — cr:toggle:<id> в лимите."""
    rules = [
        {"id": i, "rule_type": f"rule_{i}", "is_active": i % 2 == 0,
         "description": "д" * 300}
        for i in range(1, 6)
    ]
    rows = check_kb(kb_compat_rules(rules))
    assert [cb for _, cb in rows[:-1]] == [f"cr:toggle:{i}" for i in range(1, 6)]


def test_start_and_review_static_callbacks():
    """Статические клавиатуры (kb_start, kb_review) — тоже в лимите (как есть)."""
    check_kb(kb_start())
    check_kb(kb_review({"ok": False, "missing": ["vtx"], "errors": [], "warnings": []}))


# ============================================================
# регрессия боевого краша
# ============================================================

def test_regression_crash_part_list_from_real_dump():
    """Регрессия BUTTON_DATA_INVALID: 10 САМЫХ длинных имён из REAL_EXAMPLES.

    Именно такой список (боевой дамп, 143 позиции) ронял бота: кнопки деталей
    строились из ключей-имён, и Telegram отклонял ВСЁ сообщение с клавиатурой.
    Схема b:part:<id> держит callback_data в пределах лимита при любых именах.
    """
    worst = _longest_real_names(10)
    parts = [part(i, name) for i, name in enumerate(worst, start=1)]
    rows = check_kb(kb_part_list(parts, 0, 1, "fc_esc_stack"))
    part_cbs = [cb for _, cb in rows if cb.startswith("b:part:")]
    assert part_cbs == [f"b:part:{i}" for i in range(1, 11)]
    # контроль: при старой схеме «b:part:<ключ-имя>» худший колбэк был бы > 64 байт
    worst_key = max(worst, key=len).lower()
    assert len(f"b:part:{worst_key}".encode("utf-8")) > CB_LIMIT
