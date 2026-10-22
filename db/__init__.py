"""Слой базы данных: SQLite сейчас, PostgreSQL позже (только смена DB_URL).

Состав:
  db.engine        — async-движок и фабрика сессий (aiosqlite; позже asyncpg);
  db.models        — ORM-таблицы: follow_ups, followup_reminders, calc_entries,
                     prices, faq_*, settings, users + daily_counters (аналитика),
                     user_states (FSM); «🛠 Конструктор дронов» (ЭТАП 1):
                     parts, stock, stock_snapshots + rows, part_aliases,
                     compat_rules, builder_drafts;
  db.seed          — одноразовый идемпотентный импорт из старых JSON-файлов;
  db.stock_import  — импорт CSV-остатков 1С в parts/stock (+ снапшоты);
  db.repositories  — репозитории с dict-интерфейсом для хендлеров;
  db.repositories_builder — фасады «Конструктора» (ЭТАП 4; реэкспортируются
                     в db.repositories);
  db.fsm_storage   — персистентное FSM-хранилище aiogram (вместо MemoryStorage);
  db.retry         — @retry_on_db_lock: ретраи записи при блокировке SQLite.

Публичные синглтоны: followups / calc_history / prices / faq / settings /
users / counters.
Зависимости в одну сторону: config ← db ← handlers ← bot.py (циклов нет).

Инициализация (bot.py, до старта polling):
    from db import init_db, close_db
    await init_db()          # таблицы + первичный импорт из JSON/CSV (если он
                             # ещё не сделан) + сид правил совместимости
                             # (data/builder/seed_compat.py, идемпотентно)
    ...
    await close_db()         # при остановке
"""

import logging

from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from db.engine import close_engine, ensure_engine, get_sessionmaker, init_engine
from db.models import Base
from db.repositories import (
    CalcHistoryRepository,
    DailyCounterRepository,
    FaqRepository,
    FollowUpRepository,
    PriceRepository,
    SettingsRepository,
    UserRepository,
)
from db.repositories_builder import seed_recipes
from db.seed import DEFAULT_SOURCES, SeedSources, seed_from_json
from db.stock_import import (
    ensure_synthetic_parts,
    import_stock_csv,
    seed_stock_if_empty,
)
from data.builder.seed_compat import seed_compat_rules

system_logger = logging.getLogger("system")

# Репозитории — синглтоны: движок нужен только при вызовах методов, поэтому
# их можно импортировать в хендлерах до init_db().
followups = FollowUpRepository()
calc_history = CalcHistoryRepository()
prices = PriceRepository()
faq = FaqRepository()
settings = SettingsRepository()
users = UserRepository()
counters = DailyCounterRepository()


def _drafts_unique_on_user(insp) -> bool:
    """user_id в builder_drafts уникален (старая схема «один черновик на юзера»)?"""
    unique_indexes = [
        ix for ix in insp.get_indexes("builder_drafts")
        if ix.get("unique") and set(ix.get("column_names") or ()) == {"user_id"}
    ]
    unique_constraints = [
        uc for uc in insp.get_unique_constraints("builder_drafts")
        if set(uc.get("column_names") or ()) == {"user_id"}
    ]
    return bool(unique_indexes or unique_constraints)


async def _drop_leftover_indexes(conn, table_name: str) -> None:
    """DROP именованных индексов переименованной (old_e4) таблицы.

    SQLite при ALTER TABLE ... RENAME TO переносит индексы вместе с таблицей,
    сохраняя их имена: без этого CREATE TABLE заново упал бы с «index already
    exists» (автоиндексы UNIQUE переименовываются сами, именованные из
    index=True — нет).
    """
    rows = (await conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = :t "
        "AND name NOT LIKE 'sqlite_autoindex%'"
    ), {"t": table_name})).all()
    for (idx_name,) in rows:
        await conn.execute(text(f'DROP INDEX "{idx_name}"'))


async def _ensure_builder_schema(engine: AsyncEngine) -> None:
    """Точечные миграции ЭТАПА 4 (create_all НЕ alter'ит существующие таблицы).

    part_aliases: part_key из NOT NULL становится nullable (контракт add_alias:
    алиас может быть ещё не привязан к детали — SQLite ослабляет NOT NULL
    только пересозданием: rename → create по модели → перенос → drop);
    + created_by (Telegram id админа) — ADD COLUMN, данные сохраняются.
    builder_drafts: раньше «один черновик на пользователя» (user_id UNIQUE,
    колонки name не было) — теперь черновиков несколько и у них есть name
    (пересоздание с переносом, name у старых строк NULL). Идемпотентно:
    на схеме Этапа 4 — no-op.
    """
    async with engine.connect() as conn:

        def _inspect(sync_conn) -> dict:
            insp = sa_inspect(sync_conn)
            info: dict = {}
            if insp.has_table("part_aliases"):
                info["alias_cols"] = {
                    c["name"]: bool(c.get("nullable", True))
                    for c in insp.get_columns("part_aliases")
                }
            if insp.has_table("builder_drafts"):
                info["draft_cols"] = {
                    c["name"] for c in insp.get_columns("builder_drafts")
                }
                info["drafts_unique_user"] = _drafts_unique_on_user(insp)
            return info

        info = await conn.run_sync(_inspect)

    alias_cols = info.get("alias_cols") or {}
    if alias_cols and alias_cols.get("part_key", True) is False:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS part_aliases_old_e4"))
            await conn.execute(text("ALTER TABLE part_aliases RENAME TO part_aliases_old_e4"))
            await _drop_leftover_indexes(conn, "part_aliases_old_e4")
            await conn.run_sync(
                lambda sync_conn: Base.metadata.tables["part_aliases"].create(sync_conn)
            )
            created_by_src = "created_by" if "created_by" in alias_cols else "NULL"
            await conn.execute(text(
                "INSERT INTO part_aliases (id, raw_name, part_key, category_override, "
                f"created_by, created_at) SELECT id, raw_name, part_key, "
                f"category_override, {created_by_src}, created_at "
                "FROM part_aliases_old_e4"
            ))
            await conn.execute(text("DROP TABLE part_aliases_old_e4"))
    elif alias_cols and "created_by" not in alias_cols:
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE part_aliases ADD COLUMN created_by BIGINT"))

    if not info.get("draft_cols"):
        return
    if "name" in info["draft_cols"] and not info["drafts_unique_user"]:
        return
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS builder_drafts_old_e4"))
        await conn.execute(text("ALTER TABLE builder_drafts RENAME TO builder_drafts_old_e4"))
        await _drop_leftover_indexes(conn, "builder_drafts_old_e4")
        await conn.run_sync(
            lambda sync_conn: Base.metadata.tables["builder_drafts"].create(sync_conn)
        )
        name_src = "name" if "name" in info["draft_cols"] else "NULL"
        await conn.execute(text(
            "INSERT INTO builder_drafts (id, user_id, name, config, created_at, updated_at) "
            f"SELECT id, user_id, {name_src}, config, created_at, updated_at "
            "FROM builder_drafts_old_e4"
        ))
        await conn.execute(text("DROP TABLE builder_drafts_old_e4"))


async def init_db(sources: SeedSources | None = DEFAULT_SOURCES) -> dict[str, int]:
    """Создаёт движок (если ещё нет) и таблицы; при пустых таблицах — импорт из JSON.

    sources=None — без сидирования (тесты с заведомо пустой БД).
    Возвращает количество записей, добавленных сидом (0 после первого запуска).
    """
    engine = ensure_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_builder_schema(engine)   # миграции ЭТАПА 4 (create_all не alter'ит)
    counts: dict[str, int] = {}
    if sources is not None:
        counts = await seed_from_json(sources)
        # CSV-остатки 1С: только при пустой parts (первичный импорт, ЭТАП 1)
        if sources.stock_csv is not None:
            stock_added = await seed_stock_if_empty(sources.stock_csv)
            if stock_added:
                counts["stock_parts"] = stock_added
        if any(counts.values()):
            system_logger.info(f"DB seed from legacy JSON: {counts}")
    # Синтетические позиции каталога (Этап 10в: гекса-рамы, отсутствующие в
    # выгрузке 1С) — досыпаем всегда, идемпотентно (existing DB тоже).
    synthetic_added = 0
    async with get_sessionmaker()() as session:
        synthetic_added = await ensure_synthetic_parts(session)
        if synthetic_added:
            await session.commit()
    if synthetic_added:
        counts["synthetic_parts"] = synthetic_added
    # Правила совместимости «🛠 Конструктора» (ЭТАП 2) — часть схемы: сидируем
    # после create_all всегда (в т.ч. при sources=None), идемпотентно по
    # rule_type — существующие правила не трогаются.
    compat_added = 0
    async with get_sessionmaker()() as session:
        compat_added = await seed_compat_rules(session)
        if compat_added:
            await session.commit()
    if compat_added:
        counts["compat_rules"] = compat_added
        system_logger.info(f"Compat rules seeded: {compat_added}")
    # Производственные рецепты (ЭТАП 10) — тоже часть схемы: сидируем всегда
    # после seed_compat_rules, идемпотентно по (size, title). part_key слотов
    # резолвится из parts на момент сида; ненайденные имена попадают в отчёт
    # unresolved (в лог), рецепт всё равно создаётся (part_key=None).
    # Хотфикс 10б: переименованные заголовки шаблонов правятся на месте
    # (renamed) — дубликаты по (size, title) не создаются.
    recipes_report = {
        "created": 0, "repaired": 0, "renamed": 0, "unresolved": [],
    }
    async with get_sessionmaker()() as session:
        recipes_report = await seed_recipes(session)
    if recipes_report["created"]:
        counts["recipes"] = recipes_report["created"]
        system_logger.info(f"Recipes seeded: {recipes_report['created']}")
    if recipes_report["repaired"]:
        system_logger.warning(
            "Recipes slots repaired (Этап 10а): %s", recipes_report["repaired"]
        )
    if recipes_report["renamed"]:
        system_logger.info(
            "Recipes titles renamed in place (Этап 10б): %s",
            recipes_report["renamed"],
        )
    if recipes_report["unresolved"]:
        system_logger.warning(
            "Recipes unresolved slots: %s", recipes_report["unresolved"]
        )
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
    "seed_stock_if_empty",
    "import_stock_csv",
    "followups",
    "calc_history",
    "prices",
    "faq",
    "settings",
    "users",
    "counters",
]
