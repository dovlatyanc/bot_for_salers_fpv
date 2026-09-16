"""Общие UI-хелперы utils/ui.py: экранирование, лог, ordered_ids, отправка/правка."""

import json
import logging

from utils.ui import (
    EDIT_SAFE_LIMIT, SEPARATOR, edit_or_send, esc, log_cb, log_event,
    ordered_ids, safe_answer_long, show_screen,
)
from utils.utils import split_text
from tests.fakes import FakeCb, FakeCbMsg, FakeMsg


# ---------- экранирование и константы ----------

def test_esc():
    assert esc("<b>x & y</b>") == "&lt;b&gt;x &amp; y&lt;/b&gt;"
    assert esc('a"b\'c') == 'a"b\'c'  # quote=False — кавычки не трогаем


def test_limits():
    assert EDIT_SAFE_LIMIT == 4000  # запас к лимиту edit_text 4096
    assert SEPARATOR == "\n\n" + "—" * 30 + "\n\n"


# ---------- логирование ----------

def test_log_event_format(caplog):
    with caplog.at_level(logging.INFO, logger="dialog"):
        log_event(42, "scenarios_open", {"scenario": "list"})
    payload = json.loads(caplog.records[-1].getMessage())
    assert payload["event"] == "scenarios_open"
    assert payload["user_id"] == 42
    assert payload["scenario"] == "list"
    assert "timestamp" in payload


def test_log_cb_uses_callback_user(caplog):
    with caplog.at_level(logging.INFO, logger="dialog"):
        log_cb(FakeCb("scn:full"), "scenario_full", {"scenario": "all"})
    payload = json.loads(caplog.records[-1].getMessage())
    assert payload["user_id"] == 42 and payload["scenario"] == "all"


# ---------- порядок раздела ----------

def test_ordered_ids_filters_and_warns(caplog):
    order = ["a", "ghost", "b"]
    items = {"a": {"stop": False}, "b": {"stop": True}}
    with caplog.at_level(logging.WARNING, logger="system"):
        ids = ordered_ids(order, items, warn_format="ORDER: нет в справочнике — {missing}")
    assert ids == ["a", "b"]
    assert "['ghost']" in caplog.text
    assert ordered_ids(order, items, only_stop=True) == ["b"]


# ---------- показ экрана ----------

async def test_show_screen_answer_and_edit():
    msg = FakeMsg()
    await show_screen(msg, "text", None, edit=False, warn_prefix="w")
    assert len(msg.answers) == 1 and msg.answers[0][1].get("parse_mode") == "HTML"

    cbm = FakeCbMsg()
    await show_screen(cbm, "text", None, edit=True, warn_prefix="w")
    assert len(cbm.edits) == 1 and not cbm.answers


async def test_show_screen_edit_failure_warns_without_fallback(caplog):
    class Broken(FakeCbMsg):
        async def edit_text(self, text, **kwargs):
            raise RuntimeError("message is not modified")

    broken = Broken()
    with caplog.at_level(logging.WARNING, logger="system"):
        await show_screen(broken, "text", None, edit=True, warn_prefix="edit failed")
    assert "edit failed" in caplog.text
    assert not broken.answers  # как раньше: без фолбэка на новое сообщение


# ---------- длинные тексты ----------

async def test_safe_answer_long_swallows_errors(caplog):
    class Broken(FakeMsg):
        async def answer(self, text, **kwargs):
            raise RuntimeError("network down")

    with caplog.at_level(logging.WARNING, logger="system"):
        await safe_answer_long(Broken(), "короткий текст")
    assert "answer_long failed" in caplog.text


async def test_edit_or_send_short_edits_in_place():
    cbm = FakeCbMsg()
    await edit_or_send(cbm, "короткий", warn_prefix="w")
    assert len(cbm.edits) == 1 and not cbm.answers
    assert cbm.edits[0][1].get("parse_mode") == "HTML"


async def test_edit_or_send_edit_failure_falls_back_to_answer():
    class Broken(FakeCbMsg):
        async def edit_text(self, text, **kwargs):
            raise RuntimeError("boom")

    broken = Broken()
    await edit_or_send(broken, "короткий", warn_prefix="Scenario view edit failed")
    assert not broken.edits
    assert len(broken.answers) == 1  # answer_long одной частью


async def test_edit_or_send_long_goes_via_answer_long():
    cbm = FakeCbMsg()
    await edit_or_send(cbm, "x" * (EDIT_SAFE_LIMIT + 500))
    assert not cbm.edits
    parts = [t for t, _ in cbm.answers]
    assert all(len(p) <= 4000 for p in parts)
    assert sum(len(p) for p in parts) >= EDIT_SAFE_LIMIT


def test_split_text_boundaries():
    assert split_text("") == [""]
    parts = split_text("a" * 9000, limit=4000)
    assert all(len(p) <= 4000 for p in parts)
    assert sum(len(p) for p in parts) == 9000