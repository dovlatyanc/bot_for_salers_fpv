"""Фасады «🛠 Конструктора» (ЭТАП 4): db.repositories_builder (+ реэкспорт).

Данные готовятся import_stock_csv с CSV-фикстурами (по образцу
tests/test_stock_import.py): два файла дают added/removed/changed между
снапшотами. Проверяются форматы возврата (PART_DICT / SNAPSHOT_DICT /
DIFF_DICT / ALIAS_DICT / RULE_DICT / DRAFT_DICT) и миграции схемы Этапа 4.
"""

import asyncio
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import db
from db import _ensure_builder_schema
from db.models import Part
from db.repositories_builder import (
    add_alias,
    add_compat_rule,
    count_by_category,
    delete_alias,
    delete_draft,
    diff_snapshots,
    get_available_qty,
    get_draft,
    get_part,
    get_part_by_id,
    get_snapshot_rows,
    list_aliases,
    list_compat_rules,
    list_drafts,
    list_parts,
    list_snapshots,
    save_draft,
    set_compat_rule_active,
)
from db.stock_import import import_stock_csv

# Состав файла v1 → v2: +рама 13 (added), -термоусадка (removed, остаток 0),
# 11,000 → 8,000 у VTX (changed); остальные без изменений.
CSV_V1 = (
    "DMKR 7.2G 5W VTX, ;шт;11,000\r\n"
    "Антенна 5.8, ;шт;1,000\r\n"
    "Рама 10 дюймов, ;шт;2,000\r\n"
    "Мотор axisfly 3115 900KV, ;шт;4,000\r\n"
    "Пропеллер HQProp 10X5X3, ;шт;3,000\r\n"
    "Термоусадочная пленка ПВХ, ;кг;5,000\r\n"
)
CSV_V2 = (
    "DMKR 7.2G 5W VTX, ;шт;8,000\r\n"
    "Антенна 5.8, ;шт;1,000\r\n"
    "Рама 10 дюймов, ;шт;2,000\r\n"
    "Рама 13 дюймов, ;шт;1,000\r\n"
    "Мотор axisfly 3115 900KV, ;шт;4,000\r\n"
    "Пропеллер HQProp 10X5X3, ;шт;3,000\r\n"
)


def _write_csv(tmp_path: Path, name: str, csv_text: str) -> Path:
    path = tmp_path / name
    path.write_text(csv_text, encoding="cp1251")
    return path


async def _seed_stock(tmp_path: Path) -> tuple[int, int]:
    """Импортирует обе фикстуры; возвращает (id старого снапшота, id нового)."""
    await import_stock_csv(_write_csv(tmp_path, "v1.csv", CSV_V1))
    await import_stock_csv(_write_csv(tmp_path, "v2.csv", CSV_V2))
    snaps = await list_snapshots(limit=10)
    assert len(snaps) == 2
    return snaps[1]["id"], snaps[0]["id"]     # свежие первыми: [0] — v2


# ============================================================
# parts / stock
# ============================================================
async def test_list_parts_sorted_with_quantity(tmp_path):
    await _seed_stock(tmp_path)
    parts = await list_parts()
    assert len(parts) == 8      # 7 из CSV-фикстуры + 1 синтетическая рама (10в)
    names = [p["name"] for p in parts]
    assert names == sorted(names)                     # сортировка по name
    dmkr = next(p for p in parts if p["key"] == "dmkr 7.2g 5w vtx")
    assert (dmkr["name"], dmkr["category"], dmkr["unit"], dmkr["quantity"]) == (
        "DMKR 7.2G 5W VTX", "vtx", "шт", 8.0,
    )
    assert dmkr["params"] == {"video_band": [7.2, 7.2], "power_w": 5}
    assert isinstance(dmkr["updated_at"], str)        # ISO-строка


async def test_list_parts_category_filter(tmp_path):
    await _seed_stock(tmp_path)
    assert [p["key"] for p in await list_parts(category="frame")] == [
        "рама 10 дюймов", "рама 13 дюймов",
        "рама hex (гексакоптер)",   # синтетическая, Этап 10в — отдельная рама
    ]
    assert [p["key"] for p in await list_parts(category="vtx")] == ["dmkr 7.2g 5w vtx"]
    assert await list_parts(category="battery") == []


async def test_list_parts_in_stock(tmp_path):
    await _seed_stock(tmp_path)
    in_stock = {p["key"] for p in await list_parts(in_stock=True)}
    assert "термоусадочная пленка пвх" not in in_stock       # обнулена в v2
    assert "рама 13 дюймов" in in_stock
    assert all(p["quantity"] > 0 for p in await list_parts(in_stock=True))
    assert {p["key"] for p in await list_parts(in_stock=False)} == {
        "термоусадочная пленка пвх",
        "рама hex (гексакоптер)",   # синтетическая «под заказ», 10в
    }
    assert all(p["quantity"] <= 0 for p in await list_parts(in_stock=False))


async def test_list_parts_size_filter(tmp_path):
    await _seed_stock(tmp_path)
    assert {p["key"] for p in await list_parts(size=10)} == {
        "рама 10 дюймов", "пропеллер hqprop 10x5x3",
    }
    # size — Python-фильтр поверх выборки: сочетается с SQL-фильтром category
    assert [p["key"] for p in await list_parts(category="frame", size=13)] == [
        "рама 13 дюймов",
    ]
    assert [p["key"] for p in await list_parts(category="motor", size=10)] == []


async def test_list_parts_search_and_limit(tmp_path):
    await _seed_stock(tmp_path)
    # поиск без учёта регистра (кириллица!): фильтр в Python, не SQL LIKE
    assert [p["key"] for p in await list_parts(search="АНТЕННА")] == ["антенна 5.8"]
    assert [p["key"] for p in await list_parts(search="рама")] == [
        "рама 10 дюймов", "рама 13 дюймов", "рама hex (гексакоптер)",
    ]
    assert await list_parts(search="несуществующее") == []
    # limit — последние N после сортировки по name
    all_parts = await list_parts()
    assert await list_parts(limit=2) == all_parts[-2:]
    assert len(await list_parts(limit=100)) == 8


async def test_get_part_found_and_missing(tmp_path):
    await _seed_stock(tmp_path)
    part = await get_part("dmkr 7.2g 5w vtx")
    assert part is not None
    assert (part["name"], part["quantity"]) == ("DMKR 7.2G 5W VTX", 8.0)
    assert part["params"]["video_band"] == [7.2, 7.2]
    assert await get_part("нет такой детали") is None


async def test_get_part_by_id_found_and_missing(tmp_path):
    """Деталь по int parts.id — тот же PART_DICT, что get_part; нет id → None."""
    await _seed_stock(tmp_path)
    target = (await list_parts())[0]
    part = await get_part_by_id(target["id"])
    assert part is not None
    assert part["id"] == target["id"]
    assert part["key"] == target["key"]
    assert (part["name"], part["quantity"]) == (target["name"], target["quantity"])
    assert await get_part_by_id(10**9) is None


async def test_part_without_stock_row_has_zero_quantity(tmp_path):
    await _seed_stock(tmp_path)
    async with db.get_sessionmaker()() as session:
        session.add(Part(key="кусок квадрата", name="Кусок квадрата",
                         unit="шт", category="spare"))
        await session.commit()

    part = await get_part("кусок квадрата")
    assert part["quantity"] == 0.0                        # stock-строки нет
    assert part in await list_parts(in_stock=False)
    assert await get_available_qty("кусок квадрата") == 0.0


async def test_count_by_category_ignores_stock(tmp_path):
    await _seed_stock(tmp_path)
    # consumable обнулён в v2, но в подсчёт входит (ВСЕ части, не остатки);
    # +1 синтетическая гекса-рама (Этап 10в)
    assert await count_by_category() == {
        "antenna_air": 1, "consumable": 1, "frame": 3,
        "motor": 1, "prop": 1, "vtx": 1,
    }


async def test_get_available_qty(tmp_path):
    await _seed_stock(tmp_path)
    assert await get_available_qty("dmkr 7.2g 5w vtx") == 8.0
    assert await get_available_qty("термоусадочная пленка пвх") == 0.0
    assert await get_available_qty("нет такой детали") == 0.0


# ============================================================
# snapshots
# ============================================================
async def test_list_snapshots_fresh_first_with_limit(tmp_path):
    await _seed_stock(tmp_path)
    snaps = await list_snapshots()
    assert [s["filename"] for s in snaps] == ["v2.csv", "v1.csv"]   # свежие первыми
    assert all(isinstance(s["loaded_at"], str) for s in snaps)
    v1, v2 = snaps[1], snaps[0]
    assert (v1["total_parts"], v1["new_parts"], v1["removed_parts"]) == (6, 6, 0)
    assert (v2["total_parts"], v2["new_parts"], v2["removed_parts"]) == (6, 1, 1)
    assert len(await list_snapshots(limit=1)) == 1


async def test_get_snapshot_rows(tmp_path):
    old_id, new_id = await _seed_stock(tmp_path)
    rows = await get_snapshot_rows(new_id)
    assert len(rows) == 6
    keys = [r["part_key"] for r in rows]
    assert keys == sorted(keys)                            # по алфавиту part_key
    by_key = {r["part_key"]: r for r in rows}
    assert by_key["рама 13 дюймов"]["quantity"] == 1.0
    assert by_key["рама 13 дюймов"]["part_name"] == "Рама 13 дюймов"
    assert by_key["dmkr 7.2g 5w vtx"]["quantity"] == 8.0
    assert len(await get_snapshot_rows(old_id)) == 6


async def test_diff_snapshots(tmp_path):
    old_id, new_id = await _seed_stock(tmp_path)
    diff = await diff_snapshots(old_id, new_id)
    assert diff["added"] == [
        {"part_key": "рама 13 дюймов", "part_name": "Рама 13 дюймов", "quantity": 1.0},
    ]
    assert diff["removed"] == [
        {"part_key": "термоусадочная пленка пвх",
         "part_name": "Термоусадочная пленка ПВХ", "quantity_old": 5.0},
    ]
    assert diff["changed"] == [
        {"part_key": "dmkr 7.2g 5w vtx", "part_name": "DMKR 7.2G 5W VTX",
         "old": 11.0, "new": 8.0},
    ]
    # несуществующие снапшоты → пустые списки
    empty = await diff_snapshots(old_id, old_id)
    assert empty == {"added": [], "removed": [], "changed": []}


# ============================================================
# aliases
# ============================================================
async def test_add_alias_normalizes_and_resolves_part(tmp_path):
    await _seed_stock(tmp_path)
    alias = await add_alias("  DMKR 7.2G 5W VTX, ", created_by=42)
    assert alias["raw_name"] == "dmkr 7.2g 5w vtx"     # нормализация как part_key()
    assert alias["part_key"] == "dmkr 7.2g 5w vtx"     # автоподстановка по имени
    assert alias["category_override"] is None
    assert alias["created_by"] == 42
    assert isinstance(alias["created_at"], str)

    unknown = await add_alias("Антенна 5,8 ГГц борт", category_override="antenna_ground")
    assert unknown["part_key"] is None                 # детали с таким именем нет
    assert unknown["category_override"] == "antenna_ground"


async def test_add_alias_upsert_keeps_id_and_override(tmp_path):
    await _seed_stock(tmp_path)
    first = await add_alias("DMKR 7.2G 5W VTX, ", category_override="tx")
    # тот же raw_name после нормализации (другой регистр/пунктуация) → upsert
    second = await add_alias("  dmkR 7.2g 5W vtx ", created_by=7)
    assert second["id"] == first["id"]
    assert second["category_override"] == "tx"         # None при обновлении не затирает
    assert second["created_by"] == 7
    assert len(await list_aliases()) == 1


async def test_list_aliases_sorted_and_delete(tmp_path):
    await _seed_stock(tmp_path)
    await add_alias("DMKR 7.2G 5W VTX, ")
    await add_alias("Антенна 5.8, ")
    aliases = await list_aliases()
    assert [a["raw_name"] for a in aliases] == sorted(a["raw_name"] for a in aliases)
    assert {a["raw_name"] for a in aliases} == {"dmkr 7.2g 5w vtx", "антенна 5.8"}
    alias_id = aliases[0]["id"]
    assert await delete_alias(alias_id) is True
    assert await delete_alias(alias_id) is False       # повторно — уже нет
    assert len(await list_aliases()) == 1


# ============================================================
# compat rules
# ============================================================
async def test_list_compat_rules_seeded_five():
    rules = await list_compat_rules()
    assert len(rules) == 5                       # сид init_db (data/builder/seed_compat.py)
    assert all(r["is_active"] is True for r in rules)
    assert isinstance(rules[0]["params"], dict)


async def test_set_compat_rule_active_toggle():
    rule = (await list_compat_rules())[0]
    rule_id = rule["id"]
    assert await set_compat_rule_active(rule_id, False) is True
    assert rule_id not in {r["id"] for r in await list_compat_rules()}
    # active_only=False видит и отключённые
    assert rule_id in {r["id"] for r in await list_compat_rules(active_only=False)}
    assert await set_compat_rule_active(rule_id, True) is True
    assert rule_id in {r["id"] for r in await list_compat_rules()}
    assert await set_compat_rule_active(999_999, True) is False


async def test_add_compat_rule():
    created = await add_compat_rule("test_rule_e4", {"shaft": 5}, description="тест этапа 4")
    assert created["is_active"] is True
    assert created["params"] == {"shaft": 5}
    rules = await list_compat_rules(active_only=False)
    assert len(rules) == 6
    assert any(r["rule_type"] == "test_rule_e4" for r in rules)


# ============================================================
# drafts
# ============================================================
async def test_save_draft_creates_and_updates():
    first = await save_draft(100, {"frame": "рама 10 дюймов"}, name="Борт 10")
    assert first["id"] > 0 and first["user_id"] == 100
    assert first["name"] == "Борт 10"
    assert first["config"] == {"frame": "рама 10 дюймов"}
    assert isinstance(first["created_at"], str) and isinstance(first["updated_at"], str)

    await asyncio.sleep(0.02)                    # updated_at обязан измениться
    updated = await save_draft(
        100, {"frame": "рама 13 дюймов", "motor": "3115"}, draft_id=first["id"],
    )
    assert updated["id"] == first["id"]          # update, а не новая запись
    assert updated["config"]["frame"] == "рама 13 дюймов"
    assert updated["name"] == "Борт 10"          # name=None — подпись не тронута
    assert updated["updated_at"] > first["updated_at"]


async def test_drafts_list_get_scoped_by_user():
    d1 = await save_draft(100, {"frame": "f1"})
    d2 = await save_draft(100, {"frame": "f2"}, name="второй")
    await save_draft(200, {"frame": "f3"})

    drafts = await list_drafts(100)
    assert [d["id"] for d in drafts] == [d2["id"], d1["id"]]   # свежие первыми
    assert all(d["user_id"] == 100 for d in drafts)

    mine = await get_draft(d2["id"], 100)
    assert mine is not None and mine["name"] == "второй"
    assert await get_draft(d2["id"], 200) is None             # чужой черновик → None

    assert await delete_draft(d2["id"], 200) is False         # чужой → False
    assert await delete_draft(d2["id"], 100) is True
    assert await delete_draft(d2["id"], 100) is False
    assert await get_draft(d2["id"], 100) is None


async def test_save_draft_foreign_id_inserts_new():
    d1 = await save_draft(100, {"frame": "f1"})
    foreign = await save_draft(200, {"frame": "f9"}, draft_id=d1["id"])
    assert foreign["id"] != d1["id"]                # чужой draft_id → своя запись
    assert foreign["user_id"] == 200
    untouched = await get_draft(d1["id"], 100)
    assert untouched["config"] == {"frame": "f1"}   # чужой черновик не изменился
    assert len(await list_drafts(200)) == 1


# ============================================================
# миграции схемы Этапа 4 (init_db → _ensure_builder_schema)
# ============================================================
async def test_builder_drafts_old_schema_is_migrated(tmp_path):
    """Старая схема «один черновик на юзера» (user_id UNIQUE, без name) → Этап 4."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'mig.db').as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE builder_drafts ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " user_id BIGINT NOT NULL UNIQUE,"
                " config JSON,"
                " created_at DATETIME,"
                " updated_at DATETIME)"
            ))
            await conn.execute(text(
                "INSERT INTO builder_drafts (user_id, config, created_at, updated_at)"
                " VALUES (777, '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ))
        await _ensure_builder_schema(engine)

        async with engine.begin() as conn:
            cols = {
                row[1] for row in await conn.execute(text("PRAGMA table_info(builder_drafts)"))
            }
            assert "name" in cols
            rows = (await conn.execute(
                text("SELECT user_id, name, config FROM builder_drafts")
            )).all()
            assert rows == [(777, None, "{}")]     # строки перенесены, name = NULL
            # второй черновик того же пользователя больше не запрещён
            await conn.execute(text(
                "INSERT INTO builder_drafts (user_id, config, created_at, updated_at)"
                " VALUES (777, '{}', '2026-01-02 00:00:00', '2026-01-02 00:00:00')"
            ))
            n = (await conn.execute(text("SELECT count(*) FROM builder_drafts"))).scalar_one()
            assert n == 2
    finally:
        await engine.dispose()


async def test_part_aliases_part_key_not_null_is_relaxed(tmp_path):
    """part_aliases до Этапа 4: part_key NOT NULL, created_by нет → пересоздание.

    Главный эффект миграции: в таблицу можно писать part_key=None (алиас ещё
    не привязан к детали), старые строки и уникальность raw_name сохранены.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'mig2.db').as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE part_aliases ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " raw_name VARCHAR(512) NOT NULL UNIQUE,"
                " part_key VARCHAR(256) NOT NULL,"
                " category_override VARCHAR(64),"
                " created_at DATETIME)"
            ))
            await conn.execute(text(
                "INSERT INTO part_aliases (raw_name, part_key, created_at)"
                " VALUES ('антенна 5.8', 'антенна 5.8', '2026-01-01 00:00:00')"
            ))
        await _ensure_builder_schema(engine)

        async with engine.begin() as conn:
            info = {                                   # имя колонки → notnull
                row[1]: row[3]
                for row in await conn.execute(text("PRAGMA table_info(part_aliases)"))
            }
            assert "created_by" in info
            assert info["part_key"] == 0               # NOT NULL снят
            assert info["raw_name"] == 1               # уникальность сохранена
            rows = (await conn.execute(
                text("SELECT raw_name, part_key, created_by FROM part_aliases")
            )).all()
            assert rows == [("антенна 5.8", "антенна 5.8", None)]
            # временная таблица переноса убрана
            leftovers = (await conn.execute(text(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
                " AND name LIKE 'part_aliases_old%'"
            ))).scalar_one()
            assert leftovers == 0
            # и теперь part_key может быть NULL
            await conn.execute(text(
                "INSERT INTO part_aliases (raw_name, part_key, created_at)"
                " VALUES (' vtx dmkr ', NULL, '2026-01-02 00:00:00')"
            ))
    finally:
        await engine.dispose()


async def test_part_aliases_created_by_is_added(tmp_path):
    """part_aliases с уже nullable part_key, но без created_by → только ADD COLUMN."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'mig3.db').as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE part_aliases ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " raw_name VARCHAR(512) NOT NULL UNIQUE,"
                " part_key VARCHAR(256),"
                " category_override VARCHAR(64),"
                " created_at DATETIME)"
            ))
            await conn.execute(text(
                "INSERT INTO part_aliases (raw_name, part_key, created_at)"
                " VALUES ('антенна 5.8', 'антенна 5.8', '2026-01-01 00:00:00')"
            ))
        await _ensure_builder_schema(engine)

        async with engine.begin() as conn:
            cols = {
                row[1] for row in await conn.execute(text("PRAGMA table_info(part_aliases)"))
            }
            assert "created_by" in cols
            rows = (await conn.execute(
                text("SELECT raw_name, part_key, created_by FROM part_aliases")
            )).all()
            assert rows == [("антенна 5.8", "антенна 5.8", None)]
    finally:
        await engine.dispose()




