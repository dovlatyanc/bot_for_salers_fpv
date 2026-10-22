"""Ретраи операций записи при блокировке БД («database is locked», аудит п. 1.3).

WAL + PRAGMA busy_timeout=30000 (db/engine.py) почти исключают ошибку
блокировки: конкурентная запись ожидает снятия блокировки до 30 с. Но при
одновременных записях из нескольких задач (хендлеры + reminders_loop +
upcoming_loop) aiosqlite всё же может вернуть OperationalError, когда
истёк таймаут ожидания. Декоратор retry_on_db_lock догоняет такие записи
повторами с нарастающей паузой.

Повторяются ТОЛЬКО ошибки блокировки — прочие OperationalError (диск,
повреждение файла) и другие исключения поднимаются сразу: вслепую
повторять не-блокировочные сбои опасно и бессмысленно.
"""

import asyncio
import functools
import logging

from sqlalchemy.exc import OperationalError

system_logger = logging.getLogger("system")

# Фрагменты сообщений SQLite/aiosqlite о занятой БД (регистр не важен)
_LOCK_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
)


def _is_lock_error(exc: OperationalError) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _LOCK_MARKERS)


def retry_on_db_lock(max_retries: int = 3, delay: float = 0.5, backoff: float = 2.0):
    """Декоратор для async-методов записи: повтор при OperationalError-блокировке.

    max_retries — число повторов ПОСЛЕ первой попытки (всего до max_retries + 1);
    delay — пауза перед первым повтором, с; backoff — множитель паузы
    (растёт до 5 с, чтобы длинная цепочка не зависла на минуты).
    """

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            wait = delay
            for attempt in range(max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except OperationalError as exc:
                    if not _is_lock_error(exc) or attempt >= max_retries:
                        raise
                    system_logger.warning(
                        f"DB locked (попытка {attempt + 1}/{max_retries + 1}), "
                        f"повтор через {wait:.1f} с: {fn.__qualname__}: {exc}"
                    )
                    await asyncio.sleep(wait)
                    wait = min(wait * backoff, 5.0)

        return wrapper

    return decorator
