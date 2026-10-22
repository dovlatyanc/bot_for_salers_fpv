"""db.retry: ретраи записи при «database is locked» (аудит п. 1.3).

Повторяются только ошибки блокировки; прочие OperationalError и исключения
проходят насквозь. Паузы в юнит-тестах обнулены (delay=0.0) для скорости.
"""

import asyncio
import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

import db
from db import engine as db_engine
from db.retry import retry_on_db_lock


def locked_error() -> OperationalError:
    return OperationalError(
        "INSERT INTO x", {}, Exception("database is locked"),
    )


async def test_retries_then_succeeds():
    calls = []

    @retry_on_db_lock(max_retries=3, delay=0.0)
    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise locked_error()
        return "ok"

    assert await flaky() == "ok"
    assert len(calls) == 3


async def test_gives_up_after_max_retries():
    calls = []

    @retry_on_db_lock(max_retries=2, delay=0.0)
    async def always_locked():
        calls.append(1)
        raise locked_error()

    with pytest.raises(OperationalError):
        await always_locked()
    assert len(calls) == 3        # 1 попытка + 2 повтора


async def test_non_lock_operational_error_not_retried():
    calls = []

    @retry_on_db_lock(max_retries=5, delay=0.0)
    async def broken():
        calls.append(1)
        raise OperationalError("INSERT", {}, Exception("disk I/O error"))

    with pytest.raises(OperationalError):
        await broken()
    assert len(calls) == 1        # не блокировка — повторов нет


async def test_other_exceptions_propagate():
    @retry_on_db_lock(max_retries=3, delay=0.0)
    async def boom():
        raise ValueError("не про БД")

    with pytest.raises(ValueError):
        await boom()


async def test_wraps_preserves_name():
    @retry_on_db_lock(max_retries=1, delay=0.0)
    async def named():
        return 42

    assert named.__name__ == "named"
    assert await named() == 42


async def test_real_db_lock_recovered(tmp_path, monkeypatch):
    """Интеграционно: чужое соединение держит блокировку записи —
    первая попытка получает «database is locked», ретрай после снятия
    блокировки доводит запись до конца."""
    # busy_timeout=0 вместо 30000 мс: иначе первая попытка честно ждала бы
    # 30 секунд вместо мгновенной ошибки блокировки.
    def _no_busy_timeout(target, _record):
        cursor = target.cursor()               # как в db.engine._sqlite_pragmas:
        cursor.execute("PRAGMA busy_timeout=0")  # закрываем курсор, НЕ соединение
        cursor.close()

    monkeypatch.setattr(db_engine, "_sqlite_pragmas", _no_busy_timeout)
    db.init_engine(f"sqlite+aiosqlite:///{(tmp_path / 'locked.db').as_posix()}")
    try:
        await db.init_db(sources=None)

        blocker = sqlite3.connect(str(tmp_path / "locked.db"))
        blocker.execute("BEGIN IMMEDIATE")     # захват блокировки записи
        try:
            task = asyncio.create_task(db.settings.set("locked_key", "v"))
            await asyncio.sleep(0.3)           # первая попытка упрётся в замок
            blocker.rollback()                 # ...и замок снят
            await asyncio.wait_for(task, timeout=10)
        finally:
            blocker.close()

        assert await db.settings.get("locked_key") == "v"
    finally:
        await db.close_db()
