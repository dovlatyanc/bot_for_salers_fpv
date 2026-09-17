"""db.engine: формы sqlite-URL и авто-создание каталога файловой БД.

Регрессия прод-гигиены: DB_URL=sqlite:////root/fpvbot_data/bot.db (БД вне
папки кода) должен подниматься на чистом сервере — движок сам создаёт
родительский каталог, WAL-файлы кладутся рядом.
"""

from pathlib import Path

import db
from db import engine
from sqlalchemy import text


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
