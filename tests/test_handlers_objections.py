"""Флоу «💬 Возражения»: список, стоп-лист, экран, полная сводка, неизвестные id."""

import pytest

from data.objections import OBJECTIONS, OBJECTION_ORDER
from handlers.objections import (
    LIST_TEXT, STOP_LIST_TEXT, _list_kb, _objection_kb, _stop_kb,
    button_objections, callback_objection, callback_objections_list,
    callback_objections_stop,
)
from tests.fakes import FakeCb, FakeMsg, kb_data, make_state

STOP_IDS = [oid for oid in OBJECTION_ORDER if OBJECTIONS[oid].get("stop")]


async def test_button_objections_opens_list():
    msg = FakeMsg("💬 Возражения")
    await button_objections(msg)
    text, kwargs = msg.answers[0]
    assert text == LIST_TEXT and kwargs.get("parse_mode") == "HTML"
    rows = kwargs["reply_markup"].inline_keyboard
    assert len(rows) == len(OBJECTION_ORDER) + 1
    assert rows[-1][0].callback_data == "obj:stop" and rows[-1][1].callback_data == "obj:full"


def test_list_kb_marks_stop_items():
    """Точная проверка разметки: «🛑 » ставится только перед stop-возражениями
    (у двух не-стоп возражений свой emoji 🛑 — сравниваем полный текст кнопки)."""
    rows = _list_kb().inline_keyboard
    assert len(rows) == len(OBJECTION_ORDER) + 1
    for row, oid in zip(rows, OBJECTION_ORDER):
        obj = OBJECTIONS[oid]
        prefix = "🛑 " if obj.get("stop") else ""
        expected = f"{prefix}{obj.get('emoji', '')} {obj.get('title', oid)}"
        assert row[0].text == expected, oid
        assert row[0].callback_data == f"obj:{oid}"


def test_stop_kb_lists_only_stop_items():
    rows = _stop_kb().inline_keyboard
    assert [r[0].callback_data for r in rows[:-1]] == [f"obj:{oid}" for oid in STOP_IDS]
    assert rows[-1][0].callback_data == "obj:list"  # возврат к списку


@pytest.mark.parametrize("oid", OBJECTION_ORDER)
async def test_callback_objection_shows_screen(oid):
    cb = FakeCb(f"obj:{oid}")
    await callback_objection(cb)
    assert len(cb.message.edits) == 1
    text, kwargs = cb.message.edits[0]
    assert kwargs.get("parse_mode") == "HTML"
    assert OBJECTIONS[oid]["title"] in text
    assert kb_data(kwargs["reply_markup"]) == kb_data(_objection_kb(oid))
    assert cb.alerts == [(None, False)]


async def test_objections_list_and_stop_navigation():
    cb = FakeCb("obj:list")
    await callback_objections_list(cb)
    assert cb.message.edits[0][0] == LIST_TEXT
    assert cb.alerts == [(None, False)]

    cb = FakeCb("obj:stop")
    await callback_objections_stop(cb)
    text, kwargs = cb.message.edits[0]
    assert text == STOP_LIST_TEXT
    assert len(kwargs["reply_markup"].inline_keyboard) == len(STOP_IDS) + 1
    assert cb.alerts == [(None, False)]


async def test_objections_full_plain_parts():
    cb = FakeCb("obj:full")
    await callback_objection(cb)
    parts = [t for t, _ in cb.message.answers]
    assert parts, "сводка не отправлена"
    assert all(len(p) <= 4000 for p in parts)
    assert sum(len(p) for p in parts) > 8000
    assert cb.alerts == [(None, False)]


async def test_objection_unknown_alerts():
    cb = FakeCb("obj:nope")
    await callback_objection(cb)
    assert not cb.message.edits and not cb.message.answers
    assert cb.alerts and cb.alerts[0][1] is True