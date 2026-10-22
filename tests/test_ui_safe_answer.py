"""Этап 11д: safe_answer — ack колбэка не роняет хендлер.

Боевой краш: handlers/builder.py cb_size → await callback.answer() →
TelegramBadRequest: query is too old and response timeout expired or query ID
is invalid (апдейт обработан позже ~30-секундного окна колбэка после сетевой
просадки). safe_answer проглатывает просроченный/сетевой ack с warning,
настоящие ошибки (BUTTON_DATA_INVALID) ре-райзит.
"""

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from tests.fakes import FakeCb
from utils.ui import safe_answer

OLD_QUERY = (
    "Bad Request: query is too old and response timeout expired "
    "or query ID is invalid"
)


class BrokenCb(FakeCb):
    """Колбэк, у которого answer() всегда падает заданной ошибкой."""

    def __init__(self, data, exc):
        super().__init__(data)
        self._exc = exc

    async def answer(self, text=None, show_alert=False):
        raise self._exc


class RecordingCb(FakeCb):
    """Колбэк, запоминающий аргументы answer()."""

    async def answer(self, text=None, show_alert=False):
        self.alerts.append((text, show_alert))


async def test_old_query_swallowed():
    cb = BrokenCb("b:size:10", TelegramBadRequest(method=None, message=OLD_QUERY))
    assert await safe_answer(cb) is None
    assert await safe_answer(cb, "текст", show_alert=True) is None


async def test_response_timeout_swallowed():
    cb = BrokenCb(
        "b:size:10",
        TelegramBadRequest(method=None, message="Bad Request: response timeout expired"),
    )
    assert await safe_answer(cb) is None


async def test_query_id_invalid_swallowed():
    cb = BrokenCb(
        "b:size:10",
        TelegramBadRequest(method=None, message="Bad Request: query ID is invalid"),
    )
    assert await safe_answer(cb) is None


async def test_real_bad_request_reraised():
    cb = BrokenCb(
        "b:size:10",
        TelegramBadRequest(method=None, message="Bad Request: BUTTON_DATA_INVALID"),
    )
    with pytest.raises(TelegramBadRequest):
        await safe_answer(cb)


async def test_network_error_swallowed():
    cb = BrokenCb(
        "b:size:10",
        TelegramNetworkError(method=None, message="Request timeout error"),
    )
    assert await safe_answer(cb) is None


async def test_normal_cb_gets_arguments():
    cb = RecordingCb("b:size:10")
    await safe_answer(cb, "Готово", show_alert=True)
    await safe_answer(cb)
    assert cb.alerts == [("Готово", True), (None, False)]
