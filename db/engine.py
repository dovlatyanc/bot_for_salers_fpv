"""SQLAlchemy 2.x (async): движок и фабрика сессий.

Сейчас — SQLite (aiosqlite), файл data/bot.db по умолчанию. Прод-вариант —
БД вне папки кода: DB_URL=sqlite:////root/fpvbot_data/bot.db (родительский
каталог создаётся автоматически при старте). Переход на PostgreSQL позже —
только смена DB_URL в .env (postgresql+asyncpg://...) и установка драйвера
asyncpg: схема (db/models.py) и репозитории диалектно-нейтральны.

Инициализация — bot.py при старте (init_db → init_engine). Тесты и
_verify-скрипты создают изолированный движок через init_engine(url).
"""

import logging
from pathlib import Path

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
    """sqlite+aiosqlite:// (пусто) или :memory: (в т.ч. ':///:memory:') — память."""
    tail = url.split("://", 1)[1] if "://" in url else url
    return tail.split("?", 1)[0].lstrip("/") in ("", ":memory:")


def _sqlite_file_path(url: str) -> Path | None:
    """Файл-путь из sqlite-URL или None (память/пусто).

    Формы SQLAlchemy: 3 слэша — относительный путь (sqlite:///data/bot.db),
    4 — абсолютный (sqlite:////root/fpvbot_data/bot.db). На Windows путь с
    диском пишется в 3-слэш форме: sqlite:///C:/fpvbot_data/bot.db.
    Логика продублирована в deploy/backup_db.py (там импорт config недопустим).
    """
    tail = url.split("://", 1)[1] if "://" in url else url
    tail = tail.split("?", 1)[0]
    if not tail or tail.lstrip("/") == ":memory:":
        return None
    if tail.startswith("//"):                     # 4 слэша: абсолютный путь
        tail = tail[1:]                           # '/abs/...' или '/C:/...'
    elif tail.startswith("/"):                    # 3 слэша: относительный путь
        tail = tail[1:]                           # 'data/bot.db'
    if len(tail) > 2 and tail[2] == ":":          # '/C:/...' или 'C:/...' — Windows
        tail = tail.lstrip("/")
    return Path(tail)


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
        else:
            # Файловая БД (в т.ч. вне папки кода: DB_URL=sqlite:////root/...):
            # каталог создаём при отсутствии, чтобы первый старт на «чистом»
            # сервере не падал. WAL-файлы (-wal/-shm) лягут рядом.
            db_path = _sqlite_file_path(url)
            if db_path is not None:
                db_path.parent.mkdir(parents=True, exist_ok=True)
                system_logger.info(f"DB file: {db_path}")
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
