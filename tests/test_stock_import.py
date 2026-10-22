"""db.stock_import: разбор CSV-выгрузки 1С и загрузка остатков в parts/stock.

Формат прод-файла воспроизводится дословно: cp1251, разделитель «;»,
количества с десятичной запятой и тремя знаками («11,000»), пробелы-разряды
(«1 589,000»), пустая строка-хвост «;;», запятая/кавычки внутри названий.
"""

from pathlib import Path

import db
from sqlalchemy import func, select

from db.models import Part, PartAlias, Stock, StockSnapshot, StockSnapshotRow
from db.stock_import import SYNTHETIC_KEYS
from db.seed import SeedSources
from db.stock_import import (
    ImportStats,
    import_stock_csv,
    part_key,
    parse_stock_csv,
    reclassify_all_parts,
)

# Фрагмент реальной выгрузки (cp1251): хвостовые запятые, кавычки, хвост «;;»
CSV_TEXT = (
    "DMKR 7.2G 5W VTX, ;шт;11,000\r\n"
    "Антенна 5.8, ;шт;1,000\r\n"
    "Антенна axisfly 5.8 GHz RHCP, ;шт;1 589,000\r\n"
    "\"Кусок \"\"квадрат\"\", \";шт;3,000\r\n"
    "Термоусадочная пленка из ПВХ Ширина 180 мм ? 0,15 мм (1 кг ? 12 м), ;кг;5,000\r\n"
    "Флюс гель для пайки NC-559-ASM + 3 иглы + поршень, ;компл.;500,000\r\n"
    ";;\r\n"
)


def _write_csv(path: Path, text: str, encoding: str = "cp1251") -> Path:
    path.write_text(text, encoding=encoding)
    return path


def test_part_key_normalization():
    """Ключ стабилен: регистр/пробелы/краевая пунктуация/кавычки не влияют."""
    assert part_key("DMKR 7.2G 5W VTX, ") == "dmkr 7.2g 5w vtx"
    assert part_key("  Антенна 5.8 ") == "антенна 5.8"
    # кавычки внутри имени — содержимое (csv-декодированное), ключ их сохраняет
    assert part_key('Кусок "квадрат", ') == 'кусок "квадрат"'
    # то же название другим регистром/пунктуацией — тот же ключ
    assert part_key("АНТЕННА 5.8") == part_key("антенна 5.8")


def test_parse_stock_csv_cp1251(tmp_path):
    """Прод-формат: cp1251, десятичная запятая, разряды, пропуск «;;»."""
    rows = parse_stock_csv(_write_csv(tmp_path / "s.csv", CSV_TEXT))
    assert len(rows) == 6
    first = rows[0]
    assert (first.name, first.key, first.unit, first.quantity) == (
        "DMKR 7.2G 5W VTX", "dmkr 7.2g 5w vtx", "шт", 11.0,
    )
    # «1 589,000» — пробел-разряды и запятая
    assert rows[2].quantity == 1589.0
    # единицы, отличные от шт
    assert (rows[4].unit, rows[4].quantity) == ("кг", 5.0)
    assert rows[5].unit == "компл."
    # удвоенные кавычки 1С разворачиваются, краевые срезаются
    assert rows[3].name == 'Кусок "квадрат"'
    assert rows[3].key == 'кусок "квадрат"'


def test_parse_stock_csv_utf8_also_accepted(tmp_path):
    """utf-8-файлы тоже читаются (кодировка распознаётся автоматически)."""
    rows = parse_stock_csv(
        _write_csv(tmp_path / "s.csv", "Антенна 5.8, ;шт;2,000\n", encoding="utf-8")
    )
    assert len(rows) == 1 and rows[0].quantity == 2.0


async def test_import_stock_csv_creates_parts_and_snapshot(tmp_path):
    """Первая загрузка: parts+stock созданы, снапшот с total/new; skipped учтён."""
    path = _write_csv(tmp_path / "Остатки.csv", CSV_TEXT)
    stats = await import_stock_csv(path)
    assert isinstance(stats, ImportStats)
    assert (stats.total, stats.added, stats.removed, stats.skipped) == (6, 6, 0, 1)

    sessionmaker = db.get_sessionmaker()
    async with sessionmaker() as session:
        keys = set((await session.execute(select(Part.key))).scalars().all())
        assert "dmkr 7.2g 5w vtx" in keys
        snap = (await session.execute(select(StockSnapshot))).scalars().first()
        assert (snap.filename, snap.total_parts, snap.new_parts, snap.removed_parts) == (
            "Остатки.csv", 6, 6, 0,
        )
        row = (await session.execute(
            select(StockSnapshotRow).where(
                StockSnapshotRow.part_key == "dmkr 7.2g 5w vtx"
            )
        )).scalars().first()
        assert row.quantity == 11.0


async def test_import_twice_is_idempotent(tmp_path):
    """Повторная загрузка того же файла: 0 новых, 0 обнулённых, дублей нет."""
    path = _write_csv(tmp_path / "s.csv", CSV_TEXT)
    first = await import_stock_csv(path)
    second = await import_stock_csv(path)
    assert (first.added, first.removed) == (6, 0)
    assert (second.added, second.removed) == (0, 0)

    sessionmaker = db.get_sessionmaker()
    async with sessionmaker() as session:
        n_parts = await session.scalar(select(func.count()).select_from(Part))
        assert n_parts == 7      # 6 из CSV + 1 синтетическая гекса-рама (10в)
        # снапшотов два (по одному на загрузку), строк в каждом — 6
        n_snaps = await session.scalar(select(func.count()).select_from(StockSnapshot))
        n_rows = await session.scalar(
            select(func.count()).select_from(StockSnapshotRow)
        )
        assert (n_snaps, n_rows) == (2, 12)


async def test_import_updates_quantities_and_zeroes_missing(tmp_path):
    """Новый файл: остатки перезаписываются, отсутствующая позиция → 0 (не delete)."""
    path1 = _write_csv(tmp_path / "s1.csv", CSV_TEXT)
    await import_stock_csv(path1)

    path2 = _write_csv(
        tmp_path / "s2.csv",
        "DMKR 7.2G 5W VTX, ;шт;7,000\r\nАнтенна 5.8, ;шт;0,000\r\n;;\r\n",
    )
    stats = await import_stock_csv(path2)
    assert (stats.total, stats.added, stats.removed) == (2, 0, 4)

    sessionmaker = db.get_sessionmaker()
    async with sessionmaker() as session:
        rows = (await session.execute(
            select(Part.key, Stock.quantity).join_from(
                Stock, Part, Part.id == Stock.part_id
            )
        )).all()
        qty = dict(rows)
        assert qty["dmkr 7.2g 5w vtx"] == 7.0     # обновлён
        assert qty["антенна 5.8"] == 0.0          # в файле 0,000
        assert qty['кусок "квадрат"'] == 0.0      # нет в файле → обнулён
        # позиции не удалялись (+1 синтетическая гекса-рама, Этап 10в)
        assert await session.scalar(select(func.count()).select_from(Part)) == 7


async def test_init_db_seeds_stock_only_when_parts_empty(tmp_path):
    """init_db: CSV грузится при пустой parts и НЕ грузится повторно (идемпотентно)."""
    path = _write_csv(tmp_path / "s.csv", CSV_TEXT)
    sources = SeedSources(stock_csv=path)     # остальной сид выключен

    counts1 = await db.init_db(sources=sources)
    counts2 = await db.init_db(sources=sources)
    assert counts1.get("stock_parts") == 6
    assert "stock_parts" not in counts2       # parts уже не пуста
    # синтетическая гекса-рама могла быть досыпана conftest'ом ранее —
    # на итоговый каталог это не влияет (идемпотентность ensure)
    assert "synthetic_parts" not in counts2

    sessionmaker = db.get_sessionmaker()
    async with sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Part)) == 7


async def test_ensure_synthetic_parts_hexa_frame(tmp_path):
    """Этап 10в: гекса-рама досыпается в каталог с нулевым остатком и меткой
    to_order; повторный импорт CSV флаг сохраняет. Гекса-рама — ОТДЕЛЬНАЯ
    рама «Рама HEX (Гексакоптер)»: БЕЗ params["size"] (не 10/13/15″)."""
    from db.stock_import import HEX_FRAME_NAME, ensure_synthetic_parts, part_key

    await import_stock_csv(_write_csv(tmp_path / "s.csv", CSV_TEXT))
    sessionmaker = db.get_sessionmaker()
    # флаг выживает после повторного импорта CSV (ensure идемпотентен: если
    # conftest уже досыпал раму, ensure вернёт 0 — проверяем итоговое состояние)
    await import_stock_csv(_write_csv(tmp_path / "s2.csv", CSV_TEXT))
    async with sessionmaker() as session:
        await ensure_synthetic_parts(session)
        await session.commit()
    async with sessionmaker() as session:
        part = (await session.execute(
            select(Part).where(Part.key == part_key(HEX_FRAME_NAME))
        )).scalars().one()
    assert part.category == "frame"
    assert "size" not in (part.params or {})   # размерной привязки нет
    assert part.params["to_order"] is True
    assert part.params["hexa"] is True   # маркер гекса-рамы (кнопка Hex)
    from db.models import Stock
    async with sessionmaker() as session:
        qty = (await session.execute(
            select(Stock.quantity).where(Stock.part_id == part.id)
        )).scalar()
    assert qty == 0.0


async def test_ensure_synthetic_parts_renames_legacy_hexa_frame(tmp_path):
    """Легаси-гекса-рама «Рама 15 дюймов (Гексакоптер)» (ошибочно привязана
    к 15″) переименовывается в «Рама HEX (Гексакоптер)»: id и Stock
    сохраняются, params пересчитывается без size."""
    from db.stock_import import HEX_FRAME_NAME, ensure_synthetic_parts, part_key

    await import_stock_csv(_write_csv(tmp_path / "s.csv", CSV_TEXT))
    sessionmaker = db.get_sessionmaker()
    async with sessionmaker() as session:
        parts = (await session.execute(select(Part))).scalars().all()
        hexa = next(p for p in parts if (p.params or {}).get("hexa"))
        # откатываем позицию к легаси-состоянию (как было до переименования)
        hexa.key = part_key("Рама 15 дюймов (Гексакоптер)")
        hexa.name = "Рама 15 дюймов (Гексакоптер)"
        hexa.params = {"size": 15, "to_order": True, "hexa": True}
        legacy_id = hexa.id
        await session.commit()
    async with sessionmaker() as session:
        await ensure_synthetic_parts(session)
        await session.commit()
    async with sessionmaker() as session:
        renamed = (await session.execute(
            select(Part).where(Part.key == part_key(HEX_FRAME_NAME))
        )).scalars().one()
        assert renamed.id == legacy_id            # та же позиция (id сохранён)
        assert renamed.name == HEX_FRAME_NAME
        assert "size" not in (renamed.params or {})
        assert renamed.params["to_order"] is True and renamed.params["hexa"] is True
        legacy_gone = (await session.execute(
            select(Part).where(Part.key == part_key("Рама 15 дюймов (Гексакоптер)"))
        )).scalars().first()
        assert legacy_gone is None
        from db.models import Stock
        qty = (await session.execute(
            select(Stock.quantity).where(Stock.part_id == renamed.id)
        )).scalar()
        assert qty == 0.0                          # Stock переехал вместе с позицией


async def test_seed_stock_missing_file_is_noop(tmp_path):
    """Отсутствующий файл остатков — warning, ничего не падает и не импортируется."""
    counts = await db.init_db(sources=SeedSources(stock_csv=tmp_path / "нет_файла.csv"))
    assert counts.get("stock_parts", 0) == 0


# ============================================================================
# ЭТАП 3: классификация при импорте (category/params) + reclassify_all_parts
# ============================================================================

ALIAS_CSV_TEXT = (
    "DMKR 7.2G 5W VTX, ;шт;11,000\r\n"
    "Антенна направленная 600/6000, ;шт;2,000\r\n"
    "Рама 10 МАРК4-V2, ;шт;3,000\r\n"
    "Пластик для 3D принтера PET-G, ;кг;1,500\r\n"
)


async def _get_part(key: str):
    async with db.get_sessionmaker()() as session:
        return (await session.execute(
            select(Part).where(Part.key == key)
        )).scalars().first()


async def test_import_fills_category_and_params(tmp_path):
    """Импорт проставляет category/params по классификатору (4 строки CSV)."""
    stats = await import_stock_csv(_write_csv(tmp_path / "s.csv", ALIAS_CSV_TEXT))
    assert stats.added == 4

    vtx = await _get_part("dmkr 7.2g 5w vtx")
    assert vtx.category == "vtx"
    assert vtx.params == {"video_band": [7.2, 7.2], "power_w": 5}

    antenna = await _get_part("антенна направленная 600/6000")
    assert antenna.category == "antenna_ground"
    assert antenna.params == {"video_band": [0.6, 6.0]}

    frame = await _get_part("рама 10 марк4-v2")
    assert frame.category == "frame"
    assert frame.params == {"size": 10}

    plastic = await _get_part("пластик для 3d принтера pet-g")
    assert plastic.category == "consumable"
    assert plastic.params == {}


async def test_import_recomputes_category_on_reimport(tmp_path):
    """Повторный импорт пересчитывает category/params (имя могло измениться)."""
    path = _write_csv(tmp_path / "s.csv", ALIAS_CSV_TEXT)
    await import_stock_csv(path)
    await import_stock_csv(path)

    frame = await _get_part("рама 10 марк4-v2")
    assert (frame.category, frame.params) == ("frame", {"size": 10})


async def test_alias_category_override_wins(tmp_path):
    """Алиас с category_override побеждает автоклассификацию; params — по финальной."""
    _write_csv(tmp_path / "s.csv", ALIAS_CSV_TEXT)
    async with db.get_sessionmaker()() as session:
        session.add(PartAlias(
            raw_name="DMKR 7.2G 5W VTX, ", part_key="dmkr 7.2g 5w vtx",
            category_override="tx",       # ручное исправление админа
        ))
        await session.commit()

    await import_stock_csv(tmp_path / "s.csv")

    vtx = await _get_part("dmkr 7.2g 5w vtx")
    assert vtx.category == "tx"           # не автоклассификация (vtx)
    # params пересчитаны под ФИНАЛЬНУЮ категорию tx: control_freq не распознан
    assert vtx.params == {}


async def test_alias_override_survives_reimport(tmp_path):
    """Повторный импорт с тем же алиасом СОХРАНЯЕТ переопределение."""
    path = _write_csv(tmp_path / "s.csv", ALIAS_CSV_TEXT)
    async with db.get_sessionmaker()() as session:
        session.add(PartAlias(
            raw_name="DMKR 7.2G 5W VTX, ", part_key="dmkr 7.2g 5w vtx",
            category_override="tx",
        ))
        await session.commit()

    await import_stock_csv(path)
    await import_stock_csv(path)          # повторная загрузка того же файла

    vtx = await _get_part("dmkr 7.2g 5w vtx")
    assert vtx.category == "tx"


async def test_alias_without_override_keeps_auto_category(tmp_path):
    """Алиас-синоним без category_override не мешает автоклассификации."""
    path = _write_csv(tmp_path / "s.csv", ALIAS_CSV_TEXT)
    async with db.get_sessionmaker()() as session:
        session.add(PartAlias(
            raw_name="DMKR 7.2G 5W VTX, ", part_key="dmkr 7.2g 5w vtx",
            category_override=None,
        ))
        await session.commit()

    await import_stock_csv(path)

    vtx = await _get_part("dmkr 7.2g 5w vtx")
    assert vtx.category == "vtx"
    assert vtx.params == {"video_band": [7.2, 7.2], "power_w": 5}


async def test_reclassify_all_parts_recomputes_and_reports(tmp_path):
    """reclassify: восстанавливает category/params, не трогает stock/снапшоты."""
    await import_stock_csv(_write_csv(tmp_path / "s.csv", ALIAS_CSV_TEXT))
    sessionmaker = db.get_sessionmaker()

    # портим классификацию вручную — reclassify обязан восстановить
    # (синтетическая гекса-рама — вне выгрузки, reclassify её не трогает)
    async with sessionmaker() as session:
        parts = (await session.execute(
            select(Part).where(Part.key.not_in(SYNTHETIC_KEYS))
        )).scalars().all()
        assert len(parts) == 4
        for part in parts:
            part.category = "wrong"
            part.params = {"хлам": 1}
        await session.commit()
        qty_before = dict((await session.execute(
            select(Stock.part_id, Stock.quantity)
        )).all())
        snaps_before = await session.scalar(
            select(func.count()).select_from(StockSnapshot)
        )

    async with sessionmaker() as session:
        report = await reclassify_all_parts(session)
        await session.commit()

    assert report["total"] == 4
    assert report["changed"] == 4
    assert report["by_category"] == {
        "vtx": 1, "antenna_ground": 1, "frame": 1, "consumable": 1,
    }
    assert report["other"] == []

    async with sessionmaker() as session:
        frame = (await session.execute(
            select(Part).where(Part.key == "рама 10 марк4-v2")
        )).scalars().first()
        assert (frame.category, frame.params) == ("frame", {"size": 10})
        qty_after = dict((await session.execute(
            select(Stock.part_id, Stock.quantity)
        )).all())
        snaps_after = await session.scalar(
            select(func.count()).select_from(StockSnapshot)
        )
        assert qty_after == qty_before        # stock не тронут
        assert snaps_after == snaps_before    # снапшоты не тронуты

    # повторный прогон на тех же данных — идемпотентен
    async with sessionmaker() as session:
        report2 = await reclassify_all_parts(session)
        await session.commit()
    assert (report2["total"], report2["changed"]) == (4, 0)


async def test_reclassify_reports_other(tmp_path):
    """Неопознанные позиции попадают в other (key+name) и категорию other."""
    stats = await import_stock_csv(
        _write_csv(tmp_path / "s.csv", "абракадабра 123;шт;1,000\r\n")
    )
    assert stats.added == 1

    async with db.get_sessionmaker()() as session:
        report = await reclassify_all_parts(session)
        await session.commit()

    assert report["by_category"] == {"other": 1}
    assert report["other"] == [
        {"key": "абракадабра 123", "name": "абракадабра 123"},
    ]


async def test_reclassify_respects_alias_override(tmp_path):
    """reclassify применяет те же алиас-переопределения, что и импорт."""
    path = _write_csv(tmp_path / "s.csv", ALIAS_CSV_TEXT)
    async with db.get_sessionmaker()() as session:
        session.add(PartAlias(
            raw_name="Рама 10 МАРК4-V2, ", part_key="рама 10 марк4-v2",
            category_override="spare",
        ))
        await session.commit()
    await import_stock_csv(path)

    async with db.get_sessionmaker()() as session:
        report = await reclassify_all_parts(session)
        await session.commit()

    assert report["by_category"]["spare"] == 1
    assert report["by_category"].get("frame") is None
    assert report["changed"] == 0         # импорт уже проставил то же самое
