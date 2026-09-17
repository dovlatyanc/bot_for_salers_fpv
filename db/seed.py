"""Первичный импорт данных из «старых» JSON-файлов в SQLite.

Идемпотентно: каждая таблица наполняется только если она пуста — повторные
старты ничего не дублируют. После переезда JSON-файлы остаются как резервная
копия и источником данных больше не являются. Тесты подменяют пути через
SeedSources (или выключают сид: init_db(None)).
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import BASE_DIR
from db.engine import get_sessionmaker
from db.models import (
    CalcEntry,
    FaqCategory,
    FaqQuestion,
    FollowUp,
    Price,
)
from db.repositories import PRICE_COLUMNS, _parse_date, _parse_dt

system_logger = logging.getLogger("system")


@dataclass(frozen=True)
class SeedSources:
    """Пути JSON-источников для первичного импорта (None — источник пропущен)."""

    followups: Path | None = None
    calc_history: Path | None = None
    prices: Path | None = None
    faq: Path | None = None


DEFAULT_SOURCES = SeedSources(
    followups=BASE_DIR / "data" / "followups.json",
    calc_history=BASE_DIR / "config" / "calc_history.json",
    prices=BASE_DIR / "config" / "prices.json",
    faq=BASE_DIR / "data" / "faq.json",
)


async def seed_from_json(sources: SeedSources) -> dict[str, int]:
    """Импорт в пустые таблицы; возвращает количество добавленных записей."""
    counts: dict[str, int] = {}
    async with get_sessionmaker()() as session:
        counts["followups"] = await _seed_followups(session, sources.followups)
        counts["calc_history"] = await _seed_calc_history(session, sources.calc_history)
        counts["prices"] = await _seed_prices(session, sources.prices)
        cats, questions = await _seed_faq(session, sources.faq)
        counts["faq_categories"] = cats
        counts["faq_questions"] = questions
    return counts


async def _is_empty(session: AsyncSession, model) -> bool:
    total = await session.scalar(select(func.count()).select_from(model))
    return not total


def _read_json(path: Path | None):
    """Содержимое JSON-файла или None (нет файла/битый JSON — с warning)."""
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        system_logger.warning(f"DB seed: не удалось прочитать {path.name}: {e}")
        return None


# Запись без метки времени (битые поля) — условная «эпоха» вместо now()
_FALLBACK_DT = "2000-01-01T00:00:00"


async def _seed_followups(session: AsyncSession, path: Path | None) -> int:
    """followups.json → follow_ups: id сохраняются, автоинкремент продолжится."""
    raw = _read_json(path)
    if raw is None or not isinstance(raw, dict) or not await _is_empty(session, FollowUp):
        return 0
    added = 0
    for it in raw.get("items", []):
        if not isinstance(it, dict) or not it.get("text"):
            system_logger.warning(f"DB seed followups: битая запись пропущена: {it!r}")
            continue
        try:
            user_id = int(it["user_id"])
        except (KeyError, TypeError, ValueError):
            system_logger.warning(f"DB seed followups: битый user_id: {it!r}")
            continue
        session.add(FollowUp(
            id=int(it["id"]) if it.get("id") is not None else None,
            user_id=user_id,
            text=str(it["text"]),
            due=_parse_date(it.get("due")),
            created_at=_parse_dt(it.get("created_at")) or _parse_dt(_FALLBACK_DT),
            done=bool(it.get("done")),
            done_at=_parse_dt(it.get("done_at")),
        ))
        added += 1
    if added:
        await session.commit()
    return added


async def _seed_calc_history(session: AsyncSession, path: Path | None) -> int:
    """calc_history.json → calc_entries: {user_id: [{ts, model, cost, ...}]}."""
    raw = _read_json(path)
    if raw is None or not isinstance(raw, dict) or not await _is_empty(session, CalcEntry):
        return 0
    added = 0
    for key, entries in raw.items():
        try:
            user_id = int(key)
        except (TypeError, ValueError):
            system_logger.warning(f"DB seed calc_history: битый user_id {key!r}")
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            session.add(CalcEntry(
                user_id=user_id,
                ts=_parse_dt(entry.get("ts")) or _parse_dt(_FALLBACK_DT),
                model=str(entry.get("model", "—")),
                cost=float(entry.get("cost", 0) or 0),
                price=float(entry.get("price", 0) or 0),
                margin=float(entry.get("margin", 0) or 0),
                margin_pct=float(entry.get("margin_pct", 0) or 0),
            ))
            added += 1
    if added:
        await session.commit()
    return added


async def _seed_prices(session: AsyncSession, path: Path | None) -> int:
    """prices.json → prices: {модель: число} или {модель: {cost, cash, ...}}.

    Битые записи и записи без закупочной цены (> 0) пропускаются — как в
    прежнем _load_prices.
    """
    raw = _read_json(path)
    if raw is None or not isinstance(raw, dict) or not await _is_empty(session, Price):
        return 0
    added = 0
    for code, value in raw.items():
        if isinstance(value, (int, float)):   # старый плоский формат: число = закупка
            value = {"cost": value}
        if not isinstance(value, dict):
            system_logger.warning(f"DB seed prices: некорректная запись {code}: {value!r}")
            continue
        clean: dict = {}
        for key in PRICE_COLUMNS:
            if value.get(key) is None:
                continue
            try:
                price = float(value[key])
            except (TypeError, ValueError):
                system_logger.warning(f"DB seed prices: некорректная цена {key} у {code}")
                continue
            if price > 0:
                clean[key] = price
        if clean.get("cost", 0) <= 0:
            system_logger.warning(f"DB seed prices: нет закупочной цены у {code} — пропущено")
            continue
        session.add(Price(code=str(code), **clean))
        added += 1
    if added:
        await session.commit()
    return added


async def _seed_faq(session: AsyncSession, path: Path | None) -> tuple[int, int]:
    """faq.json → faq_categories + faq_questions (порядок — как в файле)."""
    raw = _read_json(path)
    if raw is None or not isinstance(raw, dict) or not await _is_empty(session, FaqCategory):
        return 0, 0
    cats_added = questions_added = 0
    for i, cat in enumerate(raw.get("categories", [])):
        if not isinstance(cat, dict) or not cat.get("name"):
            system_logger.warning("DB seed faq: категория без name — пропущена")
            continue
        row = FaqCategory(position=i, name=str(cat["name"]))
        session.add(row)
        await session.flush()          # нужен id категории для связи вопросов
        cats_added += 1
        for j, q in enumerate(cat.get("questions", [])):
            if isinstance(q, dict) and q.get("q") and q.get("a"):
                session.add(FaqQuestion(
                    category_id=row.id, position=j, q=str(q["q"]), a=str(q["a"]),
                ))
                questions_added += 1
            else:
                system_logger.warning(f"DB seed faq: вопрос без q/a в «{row.name}» — пропущен")
    if cats_added or questions_added:
        await session.commit()
    return cats_added, questions_added


