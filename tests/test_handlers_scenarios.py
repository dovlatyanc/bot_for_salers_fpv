"""Флоу «🎯 Сценарии» на фейковых Message/CallbackQuery (паттерн _verify_scenarios.py)."""

import pytest

from data import SCENARIOS, SCENARIO_ORDER
from handlers.calculator import MarginState
from handlers.scenarios import (
    LIST_TEXT, _list_kb, _scenario_kb, button_scenarios, callback_scenario,
    callback_scenario_list,
)
from tests.fakes import FakeCb, FakeMsg, kb_data, make_state


async def test_button_scenarios_opens_list_and_clears_state():
    state = make_state()
    await state.set_state(MarginState.waiting_for_cost)  # активный FSM должен сброситься
    msg = FakeMsg("🎯 Сценарии")
    await button_scenarios(msg, state)
    assert await state.get_state() is None
    text, kwargs = msg.answers[0]
    assert text == LIST_TEXT and kwargs.get("parse_mode") == "HTML"
    cbs = [cb for _, cb in kb_data(kwargs["reply_markup"])]
    assert cbs == [f"scn:{sid}" for sid in SCENARIO_ORDER] + ["scn:full", "cheat:menu"]


def test_list_kb_layout():
    rows = _list_kb().inline_keyboard
    assert len(rows) == len(SCENARIO_ORDER) + 2  # разделы + «Всё одним» + «назад»
    assert rows[-2][0].text == "📄 Всё одним сообщением"
    assert rows[-1][0].text == "⬅️ В шпаргалки"
    assert rows[-1][0].callback_data == "cheat:menu"


@pytest.mark.parametrize("sid", SCENARIO_ORDER)
async def test_callback_scenario_shows_screen(sid):
    cb = FakeCb(f"scn:{sid}")
    await callback_scenario(cb)
    assert len(cb.message.edits) == 1
    text, kwargs = cb.message.edits[0]
    assert kwargs.get("parse_mode") == "HTML"
    assert SCENARIOS[sid]["title"] in text
    assert kb_data(kwargs["reply_markup"]) == kb_data(_scenario_kb(sid))
    assert cb.alerts == [(None, False)]  # ровно один callback.answer() без алерта


async def test_callback_scenario_list_returns_to_list():
    cb = FakeCb("scn:list")
    await callback_scenario_list(cb)
    assert cb.message.edits and cb.message.edits[0][0] == LIST_TEXT
    assert cb.alerts == [(None, False)]


async def test_callback_scenario_full_plain_parts():
    cb = FakeCb("scn:full")
    await callback_scenario(cb)
    parts = [t for t, _ in cb.message.answers]
    assert parts, "сводка не отправлена"
    assert all(len(p) <= 4000 for p in parts)  # answer_long режет по 4000
    assert sum(len(p) for p in parts) > 8000
    assert cb.alerts == [(None, False)]


async def test_callback_scenario_unknown_alerts():
    cb = FakeCb("scn:nope")
    await callback_scenario(cb)
    assert not cb.message.edits and not cb.message.answers
    assert cb.alerts and cb.alerts[0][1] is True