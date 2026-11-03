"""Контроль доступа: allowed_users-репозиторий, AccessControlMiddleware
(fail-closed, /start и /cancel), callback-хендлеры access:*, /allow и /revoke."""

from types import SimpleNamespace

import db
from db import allowed
from db.engine import get_sessionmaker
from db.models import AllowedUser
from handlers import access
from keyboards.access import APPROVE_PREFIX, DECLINE_PREFIX, kb_access_decision
from middleware import AccessControlMiddleware

ADMIN_ID = 1  # conftest.py: os.environ.setdefault("ADMIN_ID", "1")


def _user(uid: int, username="seller", first_name="Иван", last_name=None, is_bot=False):
    return SimpleNamespace(
        id=uid, username=username, first_name=first_name,
        last_name=last_name, is_bot=is_bot,
        full_name=" ".join(filter(None, (first_name, last_name))),
    )


# журнал отправок/правок ботом (общий на процесс — чистится при вызове)
SENDS: list = []
EDITS: list = []


async def _record_send(chat_id, text, **kwargs):
    SENDS.append((chat_id, text, kwargs))


async def _record_edit(text, **kwargs):
    EDITS.append((text, kwargs))


def _reset_records():
    SENDS.clear()
    EDITS.clear()


class _FakeEvent:
    """Минимальный message/callback-двойник для middleware."""

    def __init__(self, uid, text=None, callback_data=None):
        self.from_user = _user(uid)
        self.text = text
        self.bot = SimpleNamespace(send_message=_record_send)
        self.answers: list = []
        if callback_data is not None:
            # callback_query: .answer(...) и .message для edit_text
            self.message = SimpleNamespace(edit_text=_record_edit)
            self._callback_data = callback_data
        else:
            self.message = None

    @property
    def data(self):  # у CallbackQuery данные лежат в .data
        return self._callback_data

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


async def _set_status(uid: int, status: str | None) -> None:
    if status is None:
        return
    await allowed.upsert_pending(uid, None, None)
    await allowed.set_decision(uid, status)


async def _run(mw, event, allow_handler=True):
    seen = []

    async def handler(ev, data):
        seen.append(ev)
        return "handled"

    result = await mw(handler, event, {})
    return result, seen


# ---------- AllowedUserRepository ----------

async def test_upsert_and_status_roundtrip():
    assert await allowed.get_status(5001) is None
    assert await allowed.is_allowed(5001) is False
    await allowed.upsert_pending(5001, "seller", "Иван")
    assert await allowed.get_status(5001) == "pending"
    assert await allowed.is_allowed(5001) is False
    # повторный upsert не перезаписывает статус (спам-защита и «отказ не сбросить»)
    await allowed.upsert_pending(5001, "seller2", None)
    assert await allowed.get_status(5001) == "pending"
    assert await allowed.set_decision(5001, "approved") is True
    assert await allowed.is_allowed(5001) is True
    async with get_sessionmaker()() as session:
        row = await session.get(AllowedUser, 5001)
        assert row.username == "seller2"          # контакты освежились
        assert row.decided_at is not None


async def test_set_decision_missing_row_and_remove():
    assert await allowed.set_decision(6001, "approved") is False
    assert await allowed.remove_user(6001) is False
    await _set_status(6001, "approved")
    assert await allowed.remove_user(6001) is True
    assert await allowed.get_status(6001) is None


async def test_list_allowed_only_approved():
    await _set_status(6101, "approved")
    await _set_status(6102, "declined")
    await allowed.upsert_pending(6103, None, None)      # pending
    rows = await allowed.list_allowed()
    assert [r["user_id"] for r in rows] == [6101]


# ---------- AccessControlMiddleware ----------

async def test_new_user_request_sent_once():
    _reset_records()
    mw = AccessControlMiddleware()
    ev = _FakeEvent(7001, text="/start")
    result, seen = await _run(mw, ev)
    assert result is None and seen == []               # в хендлеры не пустили
    assert await allowed.get_status(7001) == "pending"
    assert any(chat == 1 for chat, _, _ in SENDS)      # админ уведомлён
    assert any("Запрос" in t for t, _ in ev.answers)
    # повторный /start: pending без повторного уведомления админу (спам-защита)
    sends_before = len(SENDS)
    ev2 = _FakeEvent(7001, text="/start")
    result, seen = await _run(mw, ev2)
    assert result is None and seen == []
    assert len(SENDS) == sends_before                  # новых уведомлений нет
    assert any("уже отправлен" in t for t, _ in ev2.answers)


async def test_declined_neutral_without_admin_notify():
    _reset_records()
    await _set_status(7002, "declined")
    mw = AccessControlMiddleware()
    ev = _FakeEvent(7002, text="/start")
    result, seen = await _run(mw, ev)
    assert result is None and seen == []
    assert SENDS == []                                 # админа не уведомляем
    assert any("нет доступа" in t for t, _ in ev.answers)


async def test_approved_passes_and_admin_always_passes():
    await _set_status(7003, "approved")
    mw = AccessControlMiddleware()
    _, seen = await _run(mw, _FakeEvent(7003, text="💰 Калькулятор"))
    assert len(seen) == 1                              # одобренный дошёл до хендлера
    _, seen = await _run(mw, _FakeEvent(ADMIN_ID, text="/stats"))
    assert len(seen) == 1                              # админ проходит всегда


async def test_fail_closed_on_db_error(monkeypatch):
    _reset_records()

    async def broken_get_status(uid):
        raise RuntimeError("DB down")

    monkeypatch.setattr(db.allowed, "get_status", broken_get_status)
    mw = AccessControlMiddleware()
    ev = _FakeEvent(7004, text="привет")
    result, seen = await _run(mw, ev)
    # fail-closed: событие НЕ дошло до хендлера (хендлер не вызван)
    assert result is None and seen == []
    # пользователь получил MSG_UNAVAILABLE
    assert ev.answers == [(AccessControlMiddleware.MSG_UNAVAILABLE, {})]


async def test_fail_closed_upsert_error_no_admin_notify(monkeypatch):
    _reset_records()

    async def broken_upsert(*args, **kwargs):
        raise RuntimeError("DB down")

    monkeypatch.setattr(db.allowed, "upsert_pending", broken_upsert)
    mw = AccessControlMiddleware()
    ev = _FakeEvent(7005, text="/start")
    result, seen = await _run(mw, ev)
    assert result is None and seen == []
    assert SENDS == []                                 # уведомление не ушло (не сохранили)
    assert any("недоступен" in t for t, _ in ev.answers)


async def test_cancel_passes_for_unauthorized():
    mw = AccessControlMiddleware()
    _, seen = await _run(mw, _FakeEvent(7006, text="/cancel"))
    assert len(seen) == 1                              # /cancel — всегда


async def test_start_never_reaches_menu_handler_but_middleware_branches():
    """Правка 3: /start неавторизованного обрабатывает middleware (ветки
    pending/declined/new), в хендлер start из menu.py он НЕ доходит."""
    _reset_records()
    await _set_status(7007, "declined")
    mw = AccessControlMiddleware()
    ev = _FakeEvent(7007, text="/start")
    result, seen = await _run(mw, ev)
    assert result is None and seen == []               # до menu.start не дошло
    assert any("нет доступа" in t for t, _ in ev.answers)


async def test_callback_query_answered_on_block():
    _reset_records()
    mw = AccessControlMiddleware()
    ev = _FakeEvent(7008, callback_data="cheat:menu")
    result, seen = await _run(mw, ev)
    assert result is None and seen == []
    assert ev.answers and ev.answers[0][1].get("show_alert") is True  # callback.answer()


async def test_callback_query_fail_closed_answered(monkeypatch):
    async def broken_get_status(uid):
        raise RuntimeError("DB down")

    monkeypatch.setattr(db.allowed, "get_status", broken_get_status)
    mw = AccessControlMiddleware()
    ev = _FakeEvent(7009, callback_data="cheat:menu")
    result, seen = await _run(mw, ev)
    assert result is None and seen == []
    assert any("недоступен" in t for t, _ in ev.answers)   # callback.answer("Сервис…")


async def test_bots_and_missing_user_pass():
    mw = AccessControlMiddleware()
    _, seen = await _run(mw, SimpleNamespace(from_user=None))
    assert len(seen) == 1
    _, seen = await _run(mw, SimpleNamespace(from_user=_user(7010, is_bot=True)))
    assert len(seen) == 1


# ---------- callback-хендлеры handlers/access.py ----------

def _cb(uid, data):
    return SimpleNamespace(
        from_user=_user(uid), data=data,
        message=SimpleNamespace(edit_text=_record_edit),
        bot=SimpleNamespace(send_message=_record_send),
        answered=[],
    )


async def _answer(cb, text=None, **kwargs):
    cb.answered.append(text)
    return None


async def _spy_answer_collector(called):
    async def spy(text=None, **kwargs):
        called.append(text)
        return None
    return spy


async def test_approve_and_decline_flow(monkeypatch):
    _reset_records()
    await allowed.upsert_pending(7101, "seller", "Иван")
    monkeypatch.setattr(access, "safe_answer", _answer)

    await access.cb_access_approve(_cb(ADMIN_ID, f"{APPROVE_PREFIX}7101"))
    assert await allowed.is_allowed(7101) is True
    assert any(chat == 7101 for chat, _, _ in SENDS)   # уведомление пользователю
    assert EDITS and "Одобрен" in EDITS[-1][0]         # карточка перерисована

    await access.cb_access_decline(_cb(ADMIN_ID, f"{DECLINE_PREFIX}7101"))
    assert await allowed.is_allowed(7101) is False
    assert await allowed.get_status(7101) == "declined"
    assert "Отклонён" in EDITS[-1][0]


async def test_callback_rejects_non_admin(monkeypatch):
    _reset_records()
    await allowed.upsert_pending(7102, None, None)
    called = []

    async def spy(callback, text=None, **kwargs):
        called.append(text)
        return None

    monkeypatch.setattr(access, "safe_answer", spy)
    await access.cb_access_approve(_cb(999, f"{APPROVE_PREFIX}7102"))
    assert called == ["Нет прав"]                      # не-админ получил отказ
    assert await allowed.get_status(7102) == "pending" # и статус не менялся


async def test_callback_invalid_id(monkeypatch):
    called = []

    async def spy(callback, text=None, **kwargs):
        called.append(text)
        return None

    monkeypatch.setattr(access, "safe_answer", spy)
    await access.cb_access_approve(_cb(ADMIN_ID, f"{APPROVE_PREFIX}abc"))
    assert called == ["Некорректная кнопка"]


# ---------- keyboards/access.py ----------

def test_callback_data_fits_telegram_limit():
    kb = kb_access_decision(123456789)
    for row in kb.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode()) <= 64


# ---------- admin-команды ----------

async def test_admin_allow_and_revoke(monkeypatch):
    from handlers import admin
    answers = []

    async def fake_answer(text=None, **kw):
        answers.append(text)

    msg = SimpleNamespace(
        from_user=_user(ADMIN_ID), text="/allow 7201 @newbie",
        answer=fake_answer,
    )
    monkeypatch.setattr(admin, "_is_admin", lambda m: True)
    await admin.cmd_allow(msg)
    assert await allowed.is_allowed(7201) is True

    msg.text = "/revoke 7201"
    await admin.cmd_revoke(msg)
    assert await allowed.get_status(7201) is None

    msg.text = "/allow abc"
    await admin.cmd_allow(msg)
    assert answers[-1].startswith("Формат:")
    # «0» и отрицательные не являются Telegram-id — тоже в отказ
    msg.text = "/allow 0"
    await admin.cmd_allow(msg)
    assert answers[-1].startswith("Формат:")
    msg.text = "/allow -5"
    await admin.cmd_allow(msg)
    assert answers[-1].startswith("Формат:")
    assert await allowed.get_status(7201) is None      # ничего не создано после отказов
