"""db.engine: формы sqlite-URL и авто-создание каталога файловой БД.

Регрессия прод-гигиены: DB_URL=sqlite:////root/fpvbot_data/bot.db (БД вне
папки кода) должен подниматься на чистом сервере — движок сам создаёт
родительский каталог, WAL-файлы кладутся рядом.

Плюс смоук ЭТАПА 1 («🛠 Конструктор дронов»): init_db создаёт все новые
таблицы (parts, stock, снапшоты, алиасы, правила, черновики) и модели
пишутся/читаются, включая JSON-поля.
"""

from pathlib import Path

import db
from db import engine
from sqlalchemy import inspect, select, text


def test_sqlite_file_path_forms():
    """Парсер пути: 4 слэша — абсолютный, 3 — относительный, память — None."""
    f = engine._sqlite_file_path
    # прод-форма из README/дока: БД вне папки кода
    assert f("sqlite:////root/fpvbot_data/bot.db") == Path("/root/fpvbot_data/bot.db")
    # относительная форма (как раньше data/bot.db)
    assert f("sqlite:///data/bot.db") == Path("data/bot.db")
    # Windows: диск в 3-слэш форме
    assert f("sqlite+aiosqlite:///C:/fpvbot_data/bot.db") == Path("C:/fpvbot_data/bot.db")
    # память и пустой хвост — не файловые БД
    assert f("sqlite+aiosqlite:///:memory:") is None
    assert f("sqlite+aiosqlite://") is None


async def test_init_engine_creates_missing_db_dir(tmp_path):
    """init_engine создаёт отсутствующий каталог файловой БД (чистый сервер)."""
    deep = tmp_path / "fpvbot_data" / "nested" / "bot.db"
    assert not deep.parent.exists()
    db.init_engine(f"sqlite+aiosqlite:///{deep.as_posix()}")
    try:
        sessionmaker = db.get_sessionmaker()
        async with sessionmaker() as session:
            assert (await session.execute(text("SELECT 1"))).scalar() == 1
        assert deep.parent.is_dir()
    finally:
        await db.close_db()


# Новые таблицы ЭТАПА 1 («🛠 Конструктор дронов»)
CONSTRUCTOR_TABLES = {
    "parts",
    "stock",
    "stock_snapshots",
    "stock_snapshot_rows",
    "part_aliases",
    "compat_rules",
    "builder_drafts",
}


async def test_init_db_creates_constructor_tables():
    """init_db создаёт все таблицы ЭТАПА 1 — в метаданных и физически в БД."""
    assert CONSTRUCTOR_TABLES <= set(db.Base.metadata.tables)
    sessionmaker = db.get_sessionmaker()
    async with sessionmaker() as session:
        conn = await session.connection()
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert CONSTRUCTOR_TABLES <= tables


async def test_constructor_models_roundtrip():
    """Смоук CRUD: Part/Stock/снапшот/алиас/правило/черновик пишутся и читаются."""
    from db.models import (
        BuilderDraft,
        CompatRule,
        Part,
        PartAlias,
        Stock,
        StockSnapshot,
        StockSnapshotRow,
    )

    sessionmaker = db.get_sessionmaker()
    async with sessionmaker() as session:
        part = Part(key="антенна 5.8", name="Антенна 5.8", unit="шт",
                    params={"freq_ghz": 5.8})
        session.add(part)
        await session.flush()
        session.add(Stock(part_id=part.id, quantity=11.0))
        snap = StockSnapshot(filename="test.csv", total_parts=1, new_parts=1)
        session.add(snap)
        await session.flush()
        session.add(StockSnapshotRow(snapshot_id=snap.id, part_key=part.key, quantity=11.0))
        session.add(PartAlias(raw_name="Антенна 5.8, ", part_key=part.key))
        session.add(CompatRule(rule_type="motor_prop", params={"shaft": 5},
                               description="тест"))
        session.add(BuilderDraft(user_id=42, config={"antenna": part.key}))
        await session.commit()
        part_id, snap_id = part.id, snap.id

    async with sessionmaker() as session:
        part = await session.get(Part, part_id)
        assert part.params == {"freq_ghz": 5.8}
        stock = await session.get(Stock, part_id)
        assert stock.quantity == 11.0
        assert stock.last_updated is not None
        row = (await session.execute(
            select(StockSnapshotRow).where(StockSnapshotRow.snapshot_id == snap_id)
        )).scalars().first()
        assert (row.part_key, row.quantity) == ("антенна 5.8", 11.0)
        draft = (await session.execute(
            select(BuilderDraft).where(BuilderDraft.user_id == 42)
        )).scalars().one()
        assert draft.config == {"antenna": "антенна 5.8"}
        assert draft.created_at is not None and draft.updated_at is not None
