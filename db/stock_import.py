"""Импорт остатков из CSV-выгрузки 1С («Остатки БАС …») в parts + stock.

Формат файла — как в выгрузке 1С: кодировка cp1251 (utf-8 тоже принимается),
разделитель «;», строки без заголовка:
    Номенклатура;Ед;Количество          →  "Антенна 5.8, ;шт;1,000"
Количество — запятая в роли десятичного разделителя и три знака после неё
(«11,000», «1 589,000»), пробелы — разряды тысяч. Пустые строки («;;»)
и строки без названия/количества пропускаются со счётчиком skipped.

Ключ позиции parts.key детерминированно нормализуется из названия
(part_key()), поэтому повторная загрузка файла обновляет остатки, а не
дублирует позиции. При каждой загрузке позиция классифицируется
(services.builder.classifier — Этап 3): category по CLASSIFICATION_RULES +
релевантные категории ключи params; переопределения
part_alias.category_override побеждают автоклассификацию всегда (и при
первом импорте, и при повторных) — это механизм ручных исправлений админа.
Тот же пересчёт для всего справочника разом — reclassify_all_parts()
(админ-команда, Этап 9). Используется двумя путями:
  * db.init_db — первичный импорт при пустой таблице parts
    (SeedSources.stock_csv → seed_stock_if_empty, идемпотентно как остальной сид);
  * загрузка нового файла в чат боту (поздний этап) — та же import_stock_csv().
Позиции, отсутствующие в новом файле, не удаляются: их остаток обнуляется.

CLI (разовый импорт вручную):
    python -m db.stock_import "путь/к/файлу.csv"   # без аргумента — DEFAULT_SOURCES
"""

import csv
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.builder.classifier import classify_part, extract_params

from db.engine import get_sessionmaker
from db.models import Part, PartAlias, Stock, StockSnapshot, StockSnapshotRow

system_logger = logging.getLogger("system")

# Краевая пунктуация имён из 1С: «DMKR 7.2G 5W VTX, » — пробелы/запятые по краям.
# Кавычки НЕ срезаем: обёртки разворачивает csv.reader, а кавычки в содержимом — данные.
_EDGE_PUNCT = " \t,.;:!?"
_NBSP = "\u00a0"


@dataclass(frozen=True)
class StockRow:
    """Одна строка файла: исходное имя, нормализованный ключ, единица, остаток."""

    name: str
    key: str
    unit: str | None
    quantity: float


@dataclass(frozen=True)
class ImportStats:
    """Итог загрузки файла (для логов и ответа боту)."""

    filename: str
    total: int      # строк-позиций в файле
    added: int      # новые позиции (parts)
    removed: int    # обнулено из-за отсутствия в файле
    skipped: int    # пропущено пустых/битых строк

    def __str__(self) -> str:
        return (
            f"{self.filename}: позиций {self.total}, новых {self.added}, "
            f"обнулено {self.removed}, пропущено {self.skipped}"
        )


def clean_name(name: str) -> str:
    """Название для отображения: NBSP → пробел, краевая пунктуация срезана.

    Регистр сохраняется («DMKR 7.2G 5W VTX, » → «DMKR 7.2G 5W VTX»).
    """
    s = str(name).replace(_NBSP, " ")
    return re.sub(r"\s+", " ", s).strip(_EDGE_PUNCT)


def part_key(name: str) -> str:
    """Название из 1С → канонический ключ parts.key (стабилен между файлами).

    clean_name() в нижнем регистре. Внутренние запятые/точки сохраняются:
    «…170мм, антенна FPV,» и «…170мм, антенна fpv» дают один ключ.
    """
    return clean_name(name).lower()


def _parse_quantity(raw: str) -> float:
    """«1 589,000» / «11,000» / «11.5» → float (ValueError — наверх)."""
    s = raw.replace(_NBSP, " ").replace(" ", "").replace(",", ".")
    return float(s)


def _decode(raw: bytes) -> str:
    """cp1251-выгрузка → текст; utf-8 пробуется первым (не перепутать кодировки)."""
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1251", errors="replace")


def _parse_full(path: Path) -> tuple[list[StockRow], int]:
    """Разбор файла → (строки, пропущено). Пустые/битые строки — warning, skip.

    Разбор через csv.reader(delimiter=";"): 1С оборачивает «сложные» названия
    в кавычки и удваивает внутренние («"НСУ ""Кузнечик"", ";шт;1,000") — csv
    разворачивает это корректно, в том числе названия, разорванные переносом строки.
    """
    rows: list[StockRow] = []
    skipped = 0
    reader = csv.reader(io.StringIO(_decode(Path(path).read_bytes())), delimiter=";")
    for lineno, fields in enumerate(reader, 1):
        name = clean_name(fields[0]) if fields else ""
        unit = fields[1].strip() if len(fields) > 1 else ""
        qty_raw = fields[2].strip() if len(fields) > 2 else ""
        if not name or not qty_raw:          # пустая строка, «;;», обрезанные
            if fields:                       # «;;»/обрезанная строка — пропущена
                skipped += 1
            continue
        try:
            quantity = _parse_quantity(qty_raw)
        except ValueError:
            system_logger.warning(
                f"stock import: {Path(path).name}:{lineno} — количество "
                f"{qty_raw!r} не число, строка пропущена"
            )
            skipped += 1
            continue
        rows.append(StockRow(name=name, key=part_key(name), unit=unit or None, quantity=quantity))
    return rows, skipped


def parse_stock_csv(path: Path) -> list[StockRow]:
    """Разбирает файл остатков 1С: список строк-позиций (пропущенные — мимо)."""
    return _parse_full(path)[0]


async def _load_overrides(session: AsyncSession) -> dict[str, str]:
    """Переопределения категорий из part_alias одним запросом.

    Возвращает {part_key(raw_name): category_override} только с непустыми
    переопределениями. Ключ — та же нормализация, что даёт ключ детали
    (part_key): «DMKR 7.2G 5W VTX, » из 1С и «DMKR 7.2G 5W VTX» из алиаса
    дают один ключ. Алиас-синоним без category_override в словарь не
    попадает и автоклассификацию не блокирует.
    """
    aliases = (await session.execute(select(PartAlias))).scalars().all()
    return {
        part_key(a.raw_name): a.category_override.strip()
        for a in aliases
        if a.category_override and a.category_override.strip()
    }


async def import_stock_csv(path: Path) -> ImportStats:
    """Загружает CSV-остатки в БД: upsert parts/stock + снапшот. Идемпотентна.

    Новые названия → Part+Stock; существующие → обновление остатка (и имени/
    единицы, если 1С их уточнил); отсутствующие в файле позиции → quantity=0.
    Повторная загрузка того же файла меняет только last_updated/updated_at.
    """
    path = Path(path)
    rows, skipped = _parse_full(path)

    # Дубли в одном файле складываются (1С их не делает — но данные есть данные)
    merged: dict[str, StockRow] = {}
    for row in rows:
        prev = merged.get(row.key)
        merged[row.key] = StockRow(
            name=prev.name if prev else row.name,
            key=row.key,
            unit=row.unit or (prev.unit if prev else None),
            quantity=row.quantity + (prev.quantity if prev else 0.0),
        )

    added = removed = 0
    async with get_sessionmaker()() as session:
        existing: dict[str, int] = dict(
            (await session.execute(select(Part.key, Part.id))).all()
        )
        overrides = await _load_overrides(session)

        def _classify(row: StockRow) -> tuple[str, dict]:
            """Категория+params строки: алиас-переопределение > автоклассификация."""
            category = overrides.get(row.key) or classify_part(row.name)
            return category, extract_params(row.name, category)

        file_keys = set(merged)

        # 1) новые позиции: Part (+ id через flush), затем их Stock
        new_rows = [r for k, r in merged.items() if k not in existing]
        if new_rows:
            classified = {r.key: _classify(r) for r in new_rows}
            session.add_all(
                Part(
                    key=r.key, name=r.name, unit=r.unit,
                    category=classified[r.key][0], params=classified[r.key][1],
                )
                for r in new_rows
            )
            await session.flush()
            created = (await session.execute(
                select(Part).where(Part.key.in_([r.key for r in new_rows]))
            )).scalars().all()
            session.add_all(
                Stock(part_id=p.id, quantity=merged[p.key].quantity) for p in created
            )
            added = len(new_rows)

        # 2) существующие: остаток перезаписывается, имя/единица — уточняются,
        #    category/params пересчитываются (имя могло измениться)
        touched = [r for k, r in merged.items() if k in existing]
        if touched:
            part_updates = []
            for r in touched:
                category, params = _classify(r)
                if r.key in SYNTHETIC_KEYS:
                    # флаги синтетики не вычисляются из имени — восстанавливаем
                    params = {**params, "to_order": True, "hexa": True}
                part_updates.append({
                    "id": existing[r.key], "name": r.name, "unit": r.unit,
                    "category": category, "params": params,
                })
            await session.execute(update(Part), part_updates)
            await session.execute(
                update(Stock),
                [{"part_id": existing[r.key], "quantity": r.quantity} for r in touched],
            )

        # 3) нет в новом файле → остаток обнуляется (позиция остаётся в каталоге)
        gone_ids = [
            existing[k] for k in existing.keys() - file_keys - SYNTHETIC_KEYS
        ]
        if gone_ids:
            await session.execute(
                update(Stock).where(Stock.part_id.in_(gone_ids)).values(quantity=0.0)
            )
            removed = len(gone_ids)

        # 4) снапшот загруженного файла (аудит) + построчный состав
        snapshot = StockSnapshot(
            filename=path.name,
            total_parts=len(rows),
            new_parts=added,
            removed_parts=removed,
        )
        session.add(snapshot)
        await session.flush()
        session.add_all(
            StockSnapshotRow(snapshot_id=snapshot.id, part_key=r.key, quantity=r.quantity)
            for r in merged.values()
        )
        await session.commit()

    stats = ImportStats(
        filename=path.name, total=len(rows), added=added, removed=removed, skipped=skipped
    )
    system_logger.info(f"DB stock import: {stats}")
    return stats


async def reclassify_all_parts(session: AsyncSession) -> dict:
    """Пересчёт category/params для ВСЕХ позиций parts (админ-команда, Этап 9).

    Логика та же, что при импорте CSV: алиас с непустым category_override
    побеждает, иначе автоклассификация по имени части; params — только
    релевантные категории ключи. Обновляются ТОЛЬКО колонки category/params:
    stock, снапшоты и алиасы не трогаются. Транзакцией владеет вызывающий
    (commit снаружи, по образцу сида Этапа 2); здесь выполняется flush.

    Отчёт: {"total": N, "by_category": {категория: количество},
    "other": [{"key", "name"} — неопознанные, на ручную разметку],
    "changed": M — сколько записей фактически изменилось (повторный прогон
    на тех же данных даёт 0)}.
    """
    parts = (await session.execute(select(Part).order_by(Part.id))).scalars().all()
    overrides = await _load_overrides(session)
    by_category: dict[str, int] = {}
    other: list[dict[str, str]] = []
    changed = 0
    total = 0
    for part in parts:
        if part.key in SYNTHETIC_KEYS:
            # синтетические позиции (Этап 10в) управляются ensure_synthetic_parts
            # и вне выгрузки 1С — reclassify их не трогает и не считает
            continue
        total += 1
        category = overrides.get(part_key(part.name)) or classify_part(part.name)
        params = extract_params(part.name, category)
        if part.category != category or (part.params or {}) != params:
            changed += 1
        part.category = category
        part.params = params
        by_category[category] = by_category.get(category, 0) + 1
        if category == "other":
            other.append({"key": part.key, "name": part.name})
    await session.flush()
    return {
        "total": total,
        "by_category": by_category,
        "other": other,
        "changed": changed,
    }


async def seed_stock_if_empty(path: Path | None) -> int:
    """Первичный импорт остатков — только при пустой таблице parts (идемпотентно)."""
    if path is None:
        return 0
    path = Path(path)
    if not path.is_file():
        system_logger.warning(f"DB seed stock: файл остатков не найден: {path}")
        return 0
    async with get_sessionmaker()() as session:
        # синтетические позиции (Этап 10в) не считаются «непустым parts»:
        # они вне CSV-выгрузки, но CSV всё равно должен импортироваться
        non_synthetic = (
            select(func.count())
            .select_from(Part)
            .where(Part.key.not_in(SYNTHETIC_KEYS))
        )
        if await session.scalar(non_synthetic):
            return 0
    return (await import_stock_csv(path)).added


# ============================================================
# Синтетические позиции каталога (Этап 10в)
# ============================================================
# Гексакоптер — это ОТДЕЛЬНАЯ рама (шесть лучей): она не относится ни к 10″,
# ни к 13″, ни к 15″ — «Hex» это тип/имя рамы, а не размер борта. В выгрузке
# 1С гекса-рам нет вовсе, но сборка гексакоптера (рецепт HEXA 4320) должна
# быть возможна: досыпаем позицию каталога с нулевым остатком и метками
# params["to_order"] и params["hexa"]; params["size"] НЕ ставим — размерной
# привязки к бортам 10/13/15″ нет, и в их подборах рамы позиция скрыта
# хотфиксом Д2. L4 (наличие) даёт по ней warning «под заказ», не блокируя
# сборку. Флаги переживают повторные загрузки CSV (import_stock_csv, шаг 2).
HEX_FRAME_NAME = "Рама HEX (Гексакоптер)"

SYNTHETIC_FRAMES: tuple[str, ...] = (HEX_FRAME_NAME,)

SYNTHETIC_KEYS: frozenset[str] = frozenset(part_key(n) for n in SYNTHETIC_FRAMES)

# Легаси-имя (ошибочно привязывало гекса-раму к 15″) — переименовывается
# в ensure_synthetic_parts (id и Stock сохраняются).
_LEGACY_HEX_FRAME_KEYS: frozenset[str] = frozenset(
    {part_key("Рама 15 дюймов (Гексакоптер)")}
)


def _synthetic_params(name: str) -> dict:
    """params синтетической позиции: автоклассификация + метки to_order и hexa."""
    category = classify_part(name)
    return {**extract_params(name, category), "to_order": True, "hexa": True}


async def ensure_synthetic_parts(session: AsyncSession) -> int:
    """Добавляет отсутствующие синтетические позиции (идемпотентно, flush без commit).

    Возвращает число реально добавленных Part (+ Stock с нулевым остатком).
    У существующих синтетических позиций восстанавливаются метки to_order/hexa,
    если пересчёт params при импорте CSV их затёр. Легаси-гекса-рама
    («Рама 15 дюймов (Гексакоптер)») переименовывается в «Рама HEX
    (Гексакоптер)» — id, Stock и алиасы сохраняются, params пересчитывается
    (привязка к 15″ снимается).
    """
    existing: dict[str, Part] = {
        p.key: p
        for p in (
            await session.execute(select(Part).where(Part.key.in_(SYNTHETIC_KEYS)))
        ).scalars()
    }
    # Миграция легаси-имени гекса-рамы (Hex — отдельная рама, не 15″).
    legacy_key = next(iter(_LEGACY_HEX_FRAME_KEYS - SYNTHETIC_KEYS), None)
    if legacy_key:
        legacy = (
            await session.execute(select(Part).where(Part.key == legacy_key))
        ).scalars().first()
        if legacy is not None and part_key(HEX_FRAME_NAME) not in existing:
            legacy.key = part_key(HEX_FRAME_NAME)
            legacy.name = HEX_FRAME_NAME
            legacy.params = _synthetic_params(HEX_FRAME_NAME)
            existing[legacy.key] = legacy
    added = 0
    new_names = []
    for name in SYNTHETIC_FRAMES:
        key = part_key(name)
        part = existing.get(key)
        if part is None:
            new_names.append(name)
        elif (part.params or {}).get("to_order") is not True or (
            part.params or {}
        ).get("hexa") is not True:
            part.params = {**(part.params or {}), "to_order": True, "hexa": True}
    if new_names:
        session.add_all(
            Part(
                key=part_key(n), name=n, unit="шт",
                category=classify_part(n), params=_synthetic_params(n),
            )
            for n in new_names
        )
        await session.flush()
        created = (
            await session.execute(
                select(Part).where(Part.key.in_(part_key(n) for n in new_names))
            )
        ).scalars().all()
        session.add_all(Stock(part_id=p.id, quantity=0.0) for p in created)
        added = len(new_names)
    await session.flush()
    if added:
        system_logger.info(f"Synthetic parts ensured: +{added}")
    return added


if __name__ == "__main__":
    import asyncio
    import sys

    from db import close_db, init_db
    from db.seed import DEFAULT_SOURCES

    async def _main() -> None:
        target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCES.stock_csv
        if target is None or not Path(target).is_file():
            raise SystemExit(f"CSV-файл остатков не найден: {target}")
        await init_db(sources=None)          # движок + таблицы, JSON-сид не нужен
        stats = await import_stock_csv(Path(target))
        print(stats)
        await close_db()

    asyncio.run(_main())
