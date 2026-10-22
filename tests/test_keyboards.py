"""Клавиатуры: раскладка ↔ MAIN_BUTTONS (регрессия «📊 История расчётов») и поиск АКБ."""

from data import CAP_CLASSES, FILTER_LABELS, PLATFORMS, VOLTAGES
from keyboards import MAIN_BUTTONS, MENU_EXIT_BUTTONS, _build_search_kb, main_kb
from keyboards.keyboards import MAIN_KEYBOARD_LAYOUT


def test_layout_matches_main_buttons():
    """Каждая кнопка раскладки обязана быть в MAIN_BUTTONS — иначе нажатие
    проваливается в AI-фолбэк (баг «📊 История» vs «📊 История расчётов»)."""
    labels = [l for row in MAIN_KEYBOARD_LAYOUT for l in row]
    assert len(labels) == len(set(labels)), "дубли кнопок в раскладке"
    assert set(labels) == set(MAIN_BUTTONS), (
        "раскладка и MAIN_BUTTONS рассинхронизированы: "
        f"лишние в раскладке: {set(labels) - set(MAIN_BUTTONS)}, "
        f"нет на клавиатуре: {set(MAIN_BUTTONS) - set(labels)}"
    )


def test_main_kb_structure():
    assert main_kb.resize_keyboard is True
    rows = [[b.text for b in row] for row in main_kb.keyboard]
    assert rows == [list(row) for row in MAIN_KEYBOARD_LAYOUT]
    texts = [t for row in rows for t in row]
    assert len(rows) == 3 and len(texts) == 9  # «🛠 Конструктор дронов» — 3-й ряд (Этап 9)
    for must in ("💰 Калькулятор", "📊 История расчётов", "📚 Шпаргалки"):
        assert must in texts, must
    # справочные разделы и каталог «📊 ТТХ» собраны под «📚 Шпаргалки»
    for gone in ("🧠 Подбор", "🎯 Сценарии", "❓ FAQ", "💬 Возражения",
                 "🛑 Стоп-лист", "📦 Памятка", "📊 ТТХ"):
        assert gone not in texts, gone


def test_legacy_ttx_still_counts_as_menu_button():
    """«📊 ТТХ» убрана из меню (каталог — в «📚 Шпаргалки», cheat:ttx), но её текст
    с закэшированной клавиатуры всё ещё ведёт к выходу из FSM-сценариев."""
    assert "📊 ТТХ" not in MAIN_BUTTONS
    assert "📊 ТТХ" in MENU_EXIT_BUTTONS


def _flat(kb):
    return [b for row in kb.inline_keyboard for b in row]


def test_search_kb_default_all():
    kb = _build_search_kb()
    btns = _flat(kb)
    # напряжения + «все», ёмкости + «все», платформы, «Все», результаты, сброс
    assert len(btns) == (len(VOLTAGES) + 1) + (len(CAP_CLASSES) + 1) + len(PLATFORMS) + 3
    assert btns[0].text == FILTER_LABELS["voltage"]["6S"]
    assert btns[0].callback_data == "srch:v=6S:d=all:c=all"
    reset = btns[-1]
    assert reset.text == "♻️ Сбросить фильтры" and reset.callback_data == "srch:reset"
    results = btns[-2]
    assert results.text == "🔍 Показать результаты"
    assert results.callback_data == "srch:show:d=all:v=all:c=all"
    assert "✅ Все" in [b.text for b in btns]  # «Все» платформ отмечена по умолчанию


def test_search_kb_active_marking():
    kb = _build_search_kb(voltage="8S", cap_class="light")
    texts = [b.text for b in _flat(kb)]
    assert "✅ 8S (29.6 В)" in texts
    assert "✅ Лёгкие (≤12Ач)" in texts
    assert "6S (22.2 В)" in texts  # без отметки


def test_search_kb_no_match_dummy_button():
    kb = _build_search_kb(drone="7", voltage="8S")
    btns = _flat(kb)
    texts = [b.text for b in btns]
    assert "Нет совпадений" in texts  # фильтрам нечего показать
    assert "🔍 Показать результаты" not in texts
    dummy = next(b for b in btns if b.text == "Нет совпадений")
    assert dummy.callback_data == "dummy"