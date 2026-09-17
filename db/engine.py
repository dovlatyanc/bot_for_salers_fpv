"""SQLAlchemy 2.x (async): движок и фабрика сессий.

Сейчас — SQLite (aiosqlite), файл data/bot.db по умолчанию. Переход на
PostgreSQL позже — только смена DB_URL в .env (postgresql+asyncpg://...)
и установка драйвера asyncpg: схема (db/models.py) и репозитории
диалектно-нейтральны.

Инициализация — bot.py при старте (init_db → init_engine). Тесты и
_verify-скрипты создают изолированный движок через init_engine(url).
"""

import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import config

system_logger = logging.getLogger("system")

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker | None = None


def default_url() -> str:
    """URL БД: DB_URL из .env, иначе SQLite в data/bot.db (от корня проекта)."""
    url = (getattr(config, "DB_URL", "") or "").strip()
    if url:
        return url
    return f"sqlite+aiosqlite:///{config.BASE_DIR / 'data' / 'bot.db'}"


def _sqlite_is_memory(url: str) -> bool:
    """sqlite+aiosqlite:// или sqlite+aiosqlite://:memory: — БД в памяти."""
    tail = url.split("://", 1)[1] if "://" in url else url
    return tail.split("?", 1)[0] in ("", ":memory:")


def _sqlite_pragmas(target, _record) -> None:
    """Прагмы на каждое соединение SQLite: WAL, внешние ключи, таймаут блокировки."""
    cursor = target.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def init_engine(url: str | None = None) -> AsyncEngine:
    """Создаёт (пересоздавая предыдущий) async-движок и фабрику сессий.

    Для БД в памяти (sqlite+aiosqlite://) используется StaticPool: одно
    соединение на весь движок, иначе каждая сессия получала бы свою пустую БД.
    """
    global _engine, _sessionmaker
    url = url or default_url()
    kwargs: dict = {}
    if url.startswith("sqlite"):
        connect_args: dict = {"timeout": 30}
        if _sqlite_is_memory(url):
            # один общий пул — иначе каждая сессия видела бы свою пустую БД
            connect_args["check_same_thread"] = False
            kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = connect_args
    _engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(_engine.sync_engine, "connect", _sqlite_pragmas)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    system_logger.info(f"DB engine ready: {url.split('://')[0]} (dialect-only log)")
    return _engine


def ensure_engine() -> AsyncEngine:
    """Движок уже создан? Иначе — создаётся с URL по умолчанию."""
    return _engine if _engine is not None else init_engine()


def get_sessionmaker() -> async_sessionmaker:
    """Фабрика сессий; до init_engine()/init_db() использование — ошибка."""
    if _sessionmaker is None:
        raise RuntimeError("DB не инициализирована — сначала db.init_db()/db.init_engine(url)")
    return _sessionmaker


async def close_engine() -> None:
    """Закрывает пул соединений и сбрасывает глобальное состояние."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
