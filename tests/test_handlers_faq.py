"""«❓ FAQ»: навигация, FSM-поиск, выход по кнопкам меню и словам отмены."""

from handlers.faq import (
    FaqState, _find, button_faq, callback_cancel, callback_category,
    callback_menu, callback_question, callback_search, get_categories,
    process_search_text, search_not_text,
)
from tests.fakes import FakeCb, FakeMsg, kb_data, make_state


async def test_load_faq_valid():
    cats = await get_categories()
    assert len(cats) >= 5
    for cat in cats:
        assert cat["name"].strip() and cat["questions"]


async def test_find_matches_and_limits():
    cats = await get_categories()
    matches = _find("гарантия", cats)
    assert matches
    assert all(0 <= ci < len(cats) for ci, _, _ in matches)
    assert len(matches) <= 10
    assert _find("аб", cats) == []       # слова короче 3 символов игнорируются
    assert _find("qqqzzz", cats) == []   # нет вхождений


async def test_button_faq_main_screen():
    state = make_state()
    await state.set_state(FaqState.waiting_for_query)  # перезапуск экрана при активном поиске
    msg = FakeMsg("❓ FAQ")
    await button_faq(msg, state)
    assert await state.get_state() is None
    text, kwargs = msg.answers[0]
    assert "FAQ" in text and kwargs.get("parse_mode") == "HTML"
    cbs = [cb for _, cb in kb_data(kwargs["reply_markup"])]
    assert cbs[0] == "faq:c:0"
    assert "faq:search" in cbs and "faq:cancel" in cbs


async def test_category_and_question_navigation():
    cats = await get_categories()
    cb = FakeCb("faq:c:0")
    await callback_category(cb)
    text, kwargs = cb.message.edits[0]
    assert cats[0]["name"] in text
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "faq:q:0:0"
    assert cb.alerts == [(None, False)]

    cb = FakeCb("faq:q:0:0")
    await callback_question(cb)
    text, _ = cb.message.edits[0]
    q0 = cats[0]["questions"][0]
    assert q0["q"] in text and q0["a"] in text
    assert cb.alerts == [(None, False)]


async def test_category_out_of_range_alerts():
    cb = FakeCb("faq:c:99")
    await callback_category(cb)
    assert not cb.message.edits
    assert cb.alerts and cb.alerts[0][1] is True


async def test_search_fsm_flow():
    state = make_state()
    cb = FakeCb("faq:search")
    await callback_search(cb, state)
    assert await state.get_state() == FaqState.waiting_for_query
    assert "Поиск по FAQ" in cb.message.edits[0][0]

    msg = FakeMsg("гарантия")
    await process_search_text(msg, state)
    assert await state.get_state() is None
    text, kwargs = msg.answers[0]
    assert "найдено" in text and kwargs.get("parse_mode") == "HTML"
    assert kb_data(kwargs["reply_markup"])[0][1].startswith("faq:q:")


async def test_search_exit_via_menu_button_and_word():
    # кнопка меню — зеркало MAIN_BUTTONS, слово «отмена» — выход из поиска
    state = make_state()
    await state.set_state(FaqState.waiting_for_query)
    msg = FakeMsg("💰 Калькулятор")
    await process_search_text(msg, state)
    assert await state.get_state() is None
    assert "FAQ закрыт" in msg.answers[0][0]

    await state.set_state(FaqState.waiting_for_query)
    msg = FakeMsg("отмена")
    await process_search_text(msg, state)
    assert await state.get_state() is None


async def test_search_repeat_faq_button_restarts():
    state = make_state()
    await state.set_state(FaqState.waiting_for_query)
    msg = FakeMsg("❓ FAQ")
    await process_search_text(msg, state)
    assert await state.get_state() is None
    assert len(msg.answers) == 1  # перезапуск главного экрана


async def test_search_non_text_hint():
    state = make_state()
    await state.set_state(FaqState.waiting_for_query)
    msg = FakeMsg(None)  # не текст (фото и т.п.)
    await search_not_text(msg, state)
    assert "Жду ключевое слово" in msg.answers[0][0]


async def test_cancel_and_menu_callbacks_clear_state():
    state = make_state()
    await state.set_state(FaqState.waiting_for_query)
    cb = FakeCb("faq:cancel")
    await callback_cancel(cb, state)
    assert await state.get_state() is None
    assert "FAQ закрыт" in cb.message.edits[0][0]

    await state.set_state(FaqState.waiting_for_query)
    cb = FakeCb("faq:menu")
    await callback_menu(cb, state)
    assert await state.get_state() is None
    assert "FAQ" in cb.message.edits[0][0]