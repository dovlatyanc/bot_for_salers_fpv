"""Сид правил совместимости (data.builder.seed_compat, ЭТАП 2) и сид
производственных рецептов в init_db (Этап 10).

Автофикстура conftest (_db) поднимает БД через db.init_db() — сид
compat-правил выполняется уже на этапе фикстуры. Поэтому сценарии «первый
сид» начинают с очистки compat_rules; идемпотентность проверяется на
повторных вызовах, а автосоздание в init_db — на заведомо пустой БД.
"""

import db
from data.builder.seed_compat import COMPAT_RULES, seed_compat_rules
from sqlalchemy import delete, func, select

from db.models import CompatRule


async def _clear_compat(sessionmaker) -> None:
    async with sessionmaker() as session:
        await session.execute(delete(CompatRule))
        await session.commit()


async def test_seed_creates_five_rules():
    """Первый вызов на пустой таблице: 5 создано, возвращает 5."""
    sessionmaker = db.get_sessionmaker()
    await _clear_compat(sessionmaker)
    async with sessionmaker() as session:
        created = await seed_compat_rules(session)
        await session.commit()
        assert created == 5
        assert await session.scalar(select(func.count()).select_from(CompatRule)) == 5


async def test_seed_idempotent_second_call():
    """Повторный вызов ничего не создаёт и не дублирует."""
    sessionmaker = db.get_sessionmaker()
    await _clear_compat(sessionmaker)
    async with sessionmaker() as session:
        assert await seed_compat_rules(session) == 5
        await session.commit()
    async with sessionmaker() as session:
        assert await seed_compat_rules(session) == 0
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(CompatRule)) == 5


async def test_seeded_rules_active_and_unique():
    """Все правила is_active=True, rule_type уникальны и совпадают с эталоном."""
    sessionmaker = db.get_sessionmaker()
    await _clear_compat(sessionmaker)
    async with sessionmaker() as session:
        assert await seed_compat_rules(session) == 5
        await session.commit()
    async with sessionmaker() as session:
        rows = (
            (await session.execute(select(CompatRule).order_by(CompatRule.id)))
            .scalars()
            .all()
        )
    assert len(rows) == 5
    assert all(rule.is_active is True for rule in rows)
    types = [rule.rule_type for rule in rows]
    assert len(set(types)) == len(types)
    assert set(types) == {spec["rule_type"] for spec in COMPAT_RULES}
    assert all(rule.description for rule in rows)
    assert all(isinstance(rule.params, dict) and rule.params for rule in rows)


async def test_init_db_seeds_rules_on_empty_db(tmp_path):
    """init_db() на пустой БД создаёт 5 правил и 13 рецептов (sources=None)."""
    await db.close_db()
    db.init_engine(f"sqlite+aiosqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    try:
        counts = await db.init_db(sources=None)
        assert counts["compat_rules"] == 5
        assert counts["recipes"] == 13
        sessionmaker = db.get_sessionmaker()
        async with sessionmaker() as session:
            assert await session.scalar(
                select(func.count()).select_from(CompatRule)
            ) == 5
        # повторный init_db — идемпотентен: новых правил и рецептов нет
        counts2 = await db.init_db(sources=None)
        assert counts2.get("compat_rules", 0) == 0
        assert counts2.get("recipes", 0) == 0
        async with sessionmaker() as session:
            assert await session.scalar(
                select(func.count()).select_from(CompatRule)
            ) == 5
    finally:
        await db.close_db()

