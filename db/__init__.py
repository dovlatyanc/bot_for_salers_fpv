"""Слой базы данных: SQLite сейчас, PostgreSQL позже (только смена DB_URL).

Состав:
  db.engine        — async-движок и фабрика сессий (aiosqlite; позже asyncpg);
  db.models        — ORM-таблицы: follow_ups, followup_reminders, calc_entries, prices, faq_*, settings;
  db.seed          — одноразовый идемпотентный импорт из старых JSON-файлов;
  db.repositories  — репозитории с dict-интерфейсом для хендлеров.

Публичные синглтоны: followups / calc_history / prices / faq / settings.
Зависимости в одну сторону: config ← db ← handlers ← bot.py (циклов нет).

Инициализация (bot.py, до старта polling):
    from db import init_db, close_db
    await init_db()          # таблицы + первичный импорт из JSON (если он ещё не сделан)
    ...
    await close_db()         # при остановке
"""

import logging

from db.engine import close_engine, ensure_engine, get_sessionmaker, init_engine
from db.models import Base
from db.repositories import (
    CalcHistoryRepository,
    FaqRepository,
    FollowUpRepository,
    PriceRepository,
    SettingsRepository,
)
from db.seed import DEFAULT_SOURCES, SeedSources, seed_from_json

system_logger = logging.getLogger("system")

# Репозитории — синглтоны: движок нужен только при вызовах методов, поэтому
# их можно импортировать в хендлерах до init_db().
followups = FollowUpRepository()
calc_history = CalcHistoryRepository()
prices = PriceRepository()
faq = FaqRepository()
settings = SettingsRepository()


async def init_db(sources: SeedSources | None = DEFAULT_SOURCES) -> dict[str, int]:
    """Создаёт движок (если ещё нет) и таблицы; при пустых таблицах — импорт из JSON.

    sources=None — без сидирования (тесты с заведомо пустой БД).
    Возвращает количество записей, добавленных сидом (0 после первого запуска).
    """
    engine = ensure_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    counts: dict[str, int] = {}
    if sources is not None:
        counts = await seed_from_json(sources)
        if any(counts.values()):
            system_logger.info(f"DB seed from legacy JSON: {counts}")
    return counts


async def close_db() -> None:
    """Закрывает пул соединений (bot.py — при остановке)."""
    await close_engine()


__all__ = [
    "Base",
    "DEFAULT_SOURCES",
    "SeedSources",
    "close_db",
    "close_engine",
    "ensure_engine",
    "get_sessionmaker",
    "init_db",
    "init_engine",
    "seed_from_json",
    "followups",
    "calc_history",
    "prices",
    "faq",
    "settings",
]
