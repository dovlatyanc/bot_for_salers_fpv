"""«📚 Шпаргалки»: inline-меню cheat:*, переиспользование экранов, FSM Подбора."""

import handlers.cheatsheets as cheats
from handlers.cheatsheets import (
    CHEATSHEET_BUTTON, CHEATSHEET_TEXT, button_cheatsheets, callback_cheat_back,
    callback_cheat_faq, callback_cheat_memo, callback_cheat_menu,
    callback_cheat_objections, callback_cheat_scenarios, callback_cheat_selection,
    callback_cheat_stoplist, callback_cheat_ttx,
)
from handlers.faq import FaqState
from handlers.menu import MEMO_TEXT, STOPLIST_TEXT, callback_row, ttx_rows_kb
from handlers.selection import SEL_STEP_A_TEXT, _sel_step_a_kb, callback_selection
from keyboards import MAIN_BUTTONS, main_kb
from states import SelectionState
from tests.fakes import FakeCb, FakeMsg, kb_data, make_state


# ---------- кнопка меню ----------

def test_cheatsheets_button_in_main_menu():
    """«📚 Шпаргалки» в MAIN_BUTTONS (иначе уйдёт в AI-фолбэк) и в main_kb;
    отдельные справочные кнопки из клавиатуры убраны."""
    assert CHEATSHEET_BUTTON in MAIN_BUTTONS
    kb_texts = [b.text for row in main_kb.keyboard for b in row]
    assert CHEATSHEET_BUTTON in kb_texts
    for gone in ("🧠 Подбор", "🎯 Сценарии", "❓ FAQ", "💬 Возражения",
                 "🛑 Стоп-лист", "📦 Памятка", "📊 ТТХ"):
        assert gone not in kb_texts, gone


async def test_button_opens_inline_menu_and_clears_fsm():
    state = make_state()
    await state.set_state(SelectionState.step_a)  # активный FSM должен сброситься
    msg = FakeMsg(CHEATSHEET_BUTTON)
    await button_cheatsheets(msg, state)
    assert await state.get_state() is None
    text, kwargs = msg.answers[0]
    assert text == CHEATSHEET_TEXT
    assert kwargs.get("parse_mode") == "HTML"
    assert [cb for _, cb in kb_data(kwargs["reply_markup"])] == [
        "cheat:ttx", "cheat:selection",
        "cheat:scenarios", "cheat:faq",
        "cheat:objections", "cheat:stoplist",
        "cheat:memo",
        "cheat:back",
    ]


# ---------- cheat:selection и «подхват» FSM Подбора ----------

async def test_cheat_selection_shows_step_a_without_set_state():
    """Экран шага А; состояние НЕ выставляется («без set_state»),
    «зависший» FSM перед входом сброшен."""
    state = make_state()
    await state.set_state(SelectionState.step_b)
    cb = FakeCb("cheat:selection")
    await callback_cheat_selection(cb, state)
    assert await state.get_state() is None
    assert cb.message.edits == [(SEL_STEP_A_TEXT, {"reply_markup": _sel_step_a_kb()})]
    assert cb.alerts == [(None, False)]


async def test_selection_fsm_picks_up_after_cheat_selection():
    """Контракт cheat:selection: после экрана из «Шпаргалок» нажатие sel:a:*
    обрабатывается существующим callback_selection → шаг B."""
    state = make_state()
    await callback_cheat_selection(FakeCb("cheat:selection"), state)
    cb = FakeCb("sel:a:6S")
    await callback_selection(cb, state)
    assert await state.get_state() == SelectionState.step_b
    buttons = kb_data(cb.message.edits[-1][1]["reply_markup"])
    assert any(cb_data.startswith("sel:b:") for _, cb_data in buttons), buttons


# ---------- переиспользование экранов разделов ----------

async def test_cheat_scenarios_opens_list():
    cb = FakeCb("cheat:scenarios")
    await callback_cheat_scenarios(cb)
    text, kwargs = cb.message.edits[0]
    assert "Сценарии продаж" in text and kwargs.get("parse_mode") == "HTML"
    assert "scn:full" in [cb_data for _, cb_data in kb_data(kwargs["reply_markup"])]


async def test_cheat_objections_opens_list():
    cb = FakeCb("cheat:objections")
    await callback_cheat_objections(cb)
    text, kwargs = cb.message.edits[0]
    assert "Возражения" in text and kwargs.get("parse_mode") == "HTML"
    assert "obj:stop" in [cb_data for _, cb_data in kb_data(kwargs["reply_markup"])]


async def test_cheat_faq_opens_main_screen_and_clears_search():
    state = make_state()
    await state.set_state(FaqState.waiting_for_query)  # активный поиск должен закрыться
    cb = FakeCb("cheat:faq")
    await callback_cheat_faq(cb, state)
    assert await state.get_state() is None
    text, kwargs = cb.message.edits[0]
    assert "FAQ" in text and kwargs.get("parse_mode") == "HTML"
    assert "faq:search" in [cb_data for _, cb_data in kb_data(kwargs["reply_markup"])]


async def test_cheat_stoplist_and_memo_reuse_menu_texts():
    """Экраны cheat:stoplist / cheat:memo — те же тексты, что у reply-кнопок."""
    for cb_data, handler, text in (
        ("cheat:stoplist", callback_cheat_stoplist, STOPLIST_TEXT),
        ("cheat:memo", callback_cheat_memo, MEMO_TEXT),
    ):
        cb = FakeCb(cb_data)
        await handler(cb)
        edit_text, edit_kwargs = cb.message.edits[0]
        assert edit_text == text
        assert edit_kwargs.get("parse_mode") == "HTML"
        assert ("⬅️ В шпаргалки", "cheat:menu") in kb_data(edit_kwargs["reply_markup"])
        assert cb.alerts == [(None, False)]


async def test_cheat_back_returns_to_main_menu():
    cb = FakeCb("cheat:back")
    await callback_cheat_back(cb)
    text, kwargs = cb.message.answers[0]
    assert "FPV Sales Bot" in text                # текст /start
    assert kwargs.get("reply_markup") is main_kb  # reply-клавиатура главного меню
    assert cb.alerts == [(None, False)]


async def test_unknown_cheat_callback_alerts():
    cb = FakeCb("cheat:nope")
    await cheats.callback_cheat_unknown(cb)
    assert cb.alerts == [("Раздел не найден", True)]


# ---------- «назад» с экранов разделов (cheat:menu) ----------

async def test_cheat_menu_callback_renders_section_menu():
    """cheat:menu — то же inline-меню, что по reply-кнопке «📚 Шпаргалки»,
    показывается правкой сообщения (edit_or_send)."""
    cb = FakeCb("cheat:menu")
    await callback_cheat_menu(cb)
    text, kwargs = cb.message.edits[0]
    assert text == CHEATSHEET_TEXT
    assert kwargs.get("parse_mode") == "HTML"
    assert [cb_data for _, cb_data in kb_data(kwargs["reply_markup"])] == [
        "cheat:ttx", "cheat:selection",
        "cheat:scenarios", "cheat:faq",
        "cheat:objections", "cheat:stoplist",
        "cheat:memo",
        "cheat:back",
    ]
    assert cb.alerts == [(None, False)]


def test_section_screens_have_back_button():
    """Единая навигация «экран → меню разделов → главное меню»: с каждого экрана
    раздела есть «⬅️ В шпаргалки» (cheat:menu) — каталог, подбор, сценарии,
    возражения, FAQ; стоп-лист/памятка проверены в тесте выше."""
    from handlers.faq import _main_kb as faq_main_kb
    from handlers.objections import _list_kb as objections_list_kb
    from handlers.scenarios import _list_kb as scenarios_list_kb

    kbs = [
        ttx_rows_kb(),                     # «Выберите ряд» (cheat:ttx / легаси «📊 ТТХ»)
        scenarios_list_kb(),               # cheat:scenarios / scn:list
        objections_list_kb(),              # cheat:objections / obj:list
        faq_main_kb([{"name": "Категория", "questions": [{"q": "вопрос", "a": "ответ"}]}]),
        _sel_step_a_kb(),                  # cheat:selection / легаси «🧠 Подбор»
    ]
    for kb in kbs:
        assert ("⬅️ В шпаргалки", "cheat:menu") in kb_data(kb), kb


# ---------- каталог моделей (экс-«📊 ТТХ») ----------

async def test_cheat_ttx_opens_row_catalog():
    """📊 Каталог моделей — экран бывшей кнопки «📊 ТТХ»: выбор ряда → row:*."""
    cb = FakeCb("cheat:ttx")
    await callback_cheat_ttx(cb)
    text, kwargs = cb.message.edits[0]
    assert text == "Выберите ряд:"
    assert kb_data(kwargs["reply_markup"]) == [
        ("6S", "row:6S"), ("8S", "row:8S"),
        ("⬅️ В шпаргалки", "cheat:menu"),
    ]
    assert cb.alerts == [(None, False)]


async def test_ttx_row_callback_lists_models():
    """Цепочка после cheat:ttx не сломана: row:6S → список моделей ряда + «назад»."""
    cb = FakeCb("row:6S")
    await callback_row(cb)
    text, kwargs = cb.message.edits[0]
    assert text == "Модели ряда 6S:"
    buttons = kb_data(kwargs["reply_markup"])
    codes = [cb_data for _, cb_data in buttons[:-1]]
    assert codes and all(c.startswith("model:") for c in codes), codes
    assert buttons[-1] == ("⬅️ К рядам", "cheat:ttx")  # назад к «Выберите ряд»