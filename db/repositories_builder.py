"""Фасады доступа к данным «🛠 Конструктора дронов» (ЭТАП 4).

Единая точка доступа хендлеров (Этапы 6–9) и сервиса совместимости (Этап 5)
к таблицам parts/stock, снапшотам, алиасам, compat-правилам и черновикам.
В отличие от классов-репозиториев db.repositories это модульные async-функции
(имена зафиксированы контрактом этапа); db.repositories реэкспортирует их —
хендлеры могут импортировать из любого из двух модулей.

Контракт возвратов — простые dict/списки («как старый JSON»), ORM наружу не
протекает:
  PART_DICT          {"id", "key", "name", "category", "unit", "params",
                      "quantity", "updated_at"}
  SNAPSHOT_DICT      {"id", "filename", "loaded_at", "total_parts",
                      "new_parts", "removed_parts"}
  SNAPSHOT_ROW_DICT  {"part_key", "part_name", "quantity"}
  DIFF_DICT          {"added":   [SNAPSHOT_ROW_DICT],
                      "removed": [{"part_key", "part_name", "quantity_old"}],
                      "changed": [{"part_key", "part_name", "old", "new"}]}
  ALIAS_DICT         {"id", "raw_name", "part_key", "category_override",
                      "created_at", "created_by"}
  RULE_DICT          {"id", "rule_type", "params", "description", "is_active"}
  DRAFT_DICT         {"id", "user_id", "name", "config", "created_at",
                      "updated_at"}
  RECIPE_DICT        {"id", "size", "title", "slots", "is_active",
                      "created_at"}; slots — список слотов рецепта
                      [{category, part_key|null, name, qty}] (Этап 10)
Все даты — ISO-строки (datetime.isoformat(), с микросекундами: updated_at
черновика обязан быть различим между апдейтами), params/config — dict.

Записи обёрнуты в @retry_on_db_lock (db/retry.py), сессии — из
db.engine.get_sessionmaker(). Нормализация названий алиасов — part_key()
из db.stock_import: тот же канонический ключ, что у деталей (НЕ дублируется).
"""

import json
from datetime import datetime

from sqlalchemy import func, select

from db.engine import get_sessionmaker
from db.models import (
    BuilderDraft,
    CompatRule,
    Part,
    PartAlias,
    Recipe,
    Stock,
    StockSnapshot,
    StockSnapshotRow,
)
from db.retry import retry_on_db_lock
from db.stock_import import part_key as normalize_part_key


def _iso(value: datetime | None) -> str | None:
    """datetime → ISO-строка (с микросекундами); None → None."""
    return value.isoformat() if value else None


# ============================================================
# parts / stock: справочник комплектующих и остатки
# ============================================================
def _part_dict(part: Part, quantity: float | None) -> dict:
    """Строка parts (+ остаток из stock) → PART_DICT."""
    return {
        "id": part.id,
        "key": part.key,
        "name": part.name,
        "category": part.category,
        "unit": part.unit,
        "params": dict(part.params or {}),
        "quantity": float(quantity) if quantity is not None else 0.0,
        "updated_at": _iso(part.updated_at),
    }


async def list_parts(
    *,
    category: str | None = None,
    size: int | None = None,
    in_stock: bool | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Список деталей с остатками, сортировка по name.

    category — SQL-фильтр. in_stock=True → quantity > 0, in_stock=False →
    quantity <= 0 (деталь без строки stock считается нулевым остатком,
    поэтому сравнение через coalesce). search — подстрока имени без учёта
    регистра и size — сравнение params["size"]: оба фильтруются В PYTHON
    поверх выборки (SQLite LIKE регистрочувствителен к кириллице, params —
    JSON без индекса; справочник маленький, ~150 позиций).
    limit — последние N ПОСЛЕ сортировки по name.
    """
    stmt = (
        select(Part, Stock.quantity)
        .outerjoin(Stock, Stock.part_id == Part.id)
        .order_by(Part.name.asc(), Part.id.asc())
    )
    if category is not None:
        stmt = stmt.where(Part.category == category)
    if in_stock is True:
        stmt = stmt.where(func.coalesce(Stock.quantity, 0.0) > 0)
    elif in_stock is False:
        stmt = stmt.where(func.coalesce(Stock.quantity, 0.0) <= 0)
    async with get_sessionmaker()() as session:
        rows = (await session.execute(stmt)).all()
    out: list[dict] = []
    for part, quantity in rows:
        if search and str(search).casefold() not in str(part.name).casefold():
            continue
        if size is not None and (part.params or {}).get("size") != size:
            continue
        out.append(_part_dict(part, quantity))
    if limit is not None:
        out = out[-int(limit):] if limit > 0 else []
    return out


async def get_part(key: str) -> dict | None:
    """Деталь по каноническому ключу (PART_DICT) или None."""
    async with get_sessionmaker()() as session:
        row = (await session.execute(
            select(Part, Stock.quantity)
            .outerjoin(Stock, Stock.part_id == Part.id)
            .where(Part.key == key)
        )).first()
    return _part_dict(row[0], row[1]) if row else None


async def get_part_by_id(part_id: int) -> dict | None:
    """Деталь по первичному ключу parts.id (PART_DICT) или None.

    Фасад для callback_data «b:part:<id>» (Этап 9а): в кнопках деталей —
    int id (лимит Telegram 64 байта на callback_data), в state/черновиках
    по-прежнему ключи-имена. Формат возврата тот же, что get_part().
    """
    async with get_sessionmaker()() as session:
        row = (await session.execute(
            select(Part, Stock.quantity)
            .outerjoin(Stock, Stock.part_id == Part.id)
            .where(Part.id == int(part_id))
        )).first()
    return _part_dict(row[0], row[1]) if row else None


async def count_by_category() -> dict[str, int]:
    """Сколько деталей в каждой категории (ВСЕ части, остатки не учитываются)."""
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(Part.category, func.count())
            .where(Part.category.is_not(None))
            .group_by(Part.category)
        )).all()
    return {str(cat): int(n) for cat, n in rows}


async def get_available_qty(key: str) -> float:
    """Остаток детали по ключу; 0.0, если нет ни детали, ни строки stock."""
    async with get_sessionmaker()() as session:
        qty = (await session.execute(
            select(Stock.quantity)
            .join(Part, Part.id == Stock.part_id)
            .where(Part.key == key)
        )).scalar()
    return float(qty) if qty is not None else 0.0


# ============================================================
# snapshots: аудит импортов CSV 1С
# ============================================================
def _snapshot_dict(snap: StockSnapshot) -> dict:
    """Строка stock_snapshots → SNAPSHOT_DICT."""
    return {
        "id": snap.id,
        "filename": snap.filename,
        "loaded_at": _iso(snap.loaded_at),
        "total_parts": int(snap.total_parts),
        "new_parts": int(snap.new_parts),
        "removed_parts": int(snap.removed_parts),
    }


def _snapshot_rows_stmt(snapshot_id: int):
    """SELECT строк снапшота с именами деталей (общий для rows и diff)."""
    return (
        select(StockSnapshotRow.part_key, StockSnapshotRow.quantity, Part.name)
        .outerjoin(Part, Part.key == StockSnapshotRow.part_key)
        .where(StockSnapshotRow.snapshot_id == snapshot_id)
        .order_by(StockSnapshotRow.part_key.asc())
    )


def _row_name(key: str, part_name: str | None) -> str:
    """Имя позиции для строки снапшота; деталь переименовали/удалили — ключ."""
    return part_name if part_name else key


async def list_snapshots(limit: int = 10) -> list[dict]:
    """Последние загрузки CSV (SNAPSHOT_DICT), свежие первыми.

    Порядок: loaded_at по убыванию, при равенстве — id по убыванию
    (две загрузки в одну секунду не меняют местами).
    """
    async with get_sessionmaker()() as session:
        rows = await session.scalars(
            select(StockSnapshot)
            .order_by(StockSnapshot.loaded_at.desc(), StockSnapshot.id.desc())
            .limit(limit)
        )
        return [_snapshot_dict(s) for s in rows]


async def get_snapshot_rows(snapshot_id: int) -> list[dict]:
    """Строки снапшота (SNAPSHOT_ROW_DICT), по алфавиту part_key."""
    async with get_sessionmaker()() as session:
        rows = (await session.execute(_snapshot_rows_stmt(snapshot_id))).all()
    return [
        {"part_key": key, "part_name": _row_name(key, name), "quantity": float(qty)}
        for key, qty, name in rows
    ]


async def diff_snapshots(old_id: int, new_id: int) -> dict:
    """Разница остатков двух снапшотов (DIFF_DICT), списки по алфавиту ключей.

    added — ключи есть в new и нет в old; removed — есть в old, нет в new
    (quantity_old из old); changed — есть в обоих и quantity различается.
    """
    async with get_sessionmaker()() as session:
        old_rows = (await session.execute(_snapshot_rows_stmt(old_id))).all()
        new_rows = (await session.execute(_snapshot_rows_stmt(new_id))).all()
    old = {key: float(qty) for key, qty, _ in old_rows}
    new = {key: float(qty) for key, qty, _ in new_rows}
    names = {key: _row_name(key, name) for key, _, name in old_rows}
    names.update({key: _row_name(key, name) for key, _, name in new_rows})
    return {
        "added": [
            {"part_key": key, "part_name": names[key], "quantity": new[key]}
            for key in sorted(set(new) - set(old))
        ],
        "removed": [
            {"part_key": key, "part_name": names[key], "quantity_old": old[key]}
            for key in sorted(set(old) - set(new))
        ],
        "changed": [
            {"part_key": key, "part_name": names[key], "old": old[key], "new": new[key]}
            for key in sorted(set(old) & set(new)) if old[key] != new[key]
        ],
    }


# ============================================================
# aliases: синонимы названий (part_aliases)
# ============================================================
def _alias_dict(alias: PartAlias) -> dict:
    """Строка part_aliases → ALIAS_DICT."""
    return {
        "id": alias.id,
        "raw_name": alias.raw_name,
        "part_key": alias.part_key,
        "category_override": alias.category_override,
        "created_at": _iso(alias.created_at),
        "created_by": alias.created_by,
    }


async def list_aliases() -> list[dict]:
    """Все алиасы (ALIAS_DICT) по алфавиту raw_name."""
    async with get_sessionmaker()() as session:
        rows = await session.scalars(
            select(PartAlias).order_by(PartAlias.raw_name.asc())
        )
        return [_alias_dict(a) for a in rows]


@retry_on_db_lock()
async def add_alias(
    raw_name: str,
    *,
    part_key: str | None = None,
    category_override: str | None = None,
    created_by: int | None = None,
) -> dict:
    """Создаёт/обновляет алиас (upsert по нормализованному raw_name) → ALIAS_DICT.

    raw_name нормализуется part_key() из db.stock_import — той же функцией,
    что и ключи деталей: повторный вызов с другим регистром/пунктуацией
    попадает в ту же строку (unique на raw_name). part_key: переданный
    аргумент сильнее; без него деталь ищется по нормализованному имени при
    КАЖДОМ вызове (есть → её key, нет → None). category_override: при
    обновлении None НЕ затирает прежнее переопределение (сброс — только
    явной перезаписью), на создании пишется как есть; пустая строка → None.
    created_by пишется только если передан.
    """
    norm = normalize_part_key(raw_name)
    override = str(category_override).strip() if category_override else None
    if not override:
        override = None
    explicit_key = str(part_key).strip() if part_key else None
    async with get_sessionmaker()() as session:
        alias = (await session.execute(
            select(PartAlias).where(PartAlias.raw_name == norm)
        )).scalars().first()
        if alias is None:
            alias = PartAlias(raw_name=norm)
            session.add(alias)
        if explicit_key is None:
            part = (await session.execute(
                select(Part).where(Part.key == norm)
            )).scalars().first()
            explicit_key = part.key if part is not None else None
        alias.part_key = explicit_key
        if alias.id is None or override is not None:
            alias.category_override = override
        if created_by is not None:
            alias.created_by = int(created_by)
        await session.commit()
        return _alias_dict(alias)


@retry_on_db_lock()
async def delete_alias(alias_id: int) -> bool:
    """Удаляет алиас по id; False — такого алиаса нет."""
    async with get_sessionmaker()() as session:
        alias = await session.get(PartAlias, alias_id)
        if alias is None:
            return False
        await session.delete(alias)
        await session.commit()
        return True


# ============================================================
# compat rules: правила совместимости (compat_rules)
# ============================================================
def _rule_dict(rule: CompatRule) -> dict:
    """Строка compat_rules → RULE_DICT."""
    return {
        "id": rule.id,
        "rule_type": rule.rule_type,
        "params": dict(rule.params or {}),
        "description": rule.description,
        "is_active": bool(rule.is_active),
    }


async def list_compat_rules(active_only: bool = True) -> list[dict]:
    """Правила совместимости (RULE_DICT) по id; active_only — только включённые."""
    stmt = select(CompatRule).order_by(CompatRule.id.asc())
    if active_only:
        stmt = stmt.where(CompatRule.is_active.is_(True))
    async with get_sessionmaker()() as session:
        rows = await session.scalars(stmt)
        return [_rule_dict(r) for r in rows]


@retry_on_db_lock()
async def set_compat_rule_active(rule_id: int, is_active: bool) -> bool:
    """Включает/отключает правило (без удаления); False — нет такого id."""
    async with get_sessionmaker()() as session:
        rule = await session.get(CompatRule, rule_id)
        if rule is None:
            return False
        rule.is_active = bool(is_active)
        await session.commit()
        return True


@retry_on_db_lock()
async def add_compat_rule(
    rule_type: str, params: dict, description: str | None = None
) -> dict:
    """Добавляет правило (включённым) → RULE_DICT.

    Уникальность rule_type не проверяется (сид data/builder/seed_compat.py
    сам идемпотентен; ручные правила админ отличает по rule_type).
    """
    async with get_sessionmaker()() as session:
        rule = CompatRule(
            rule_type=str(rule_type),
            params=dict(params or {}),
            description=description,
            is_active=True,
        )
        session.add(rule)
        await session.commit()
        return _rule_dict(rule)


# ============================================================
# drafts: черновики сборки (builder_drafts)
# ============================================================
def _draft_dict(draft: BuilderDraft) -> dict:
    """Строка builder_drafts → DRAFT_DICT."""
    return {
        "id": draft.id,
        "user_id": int(draft.user_id),
        "name": draft.name,
        "config": dict(draft.config or {}),
        "created_at": _iso(draft.created_at),
        "updated_at": _iso(draft.updated_at),
    }


@retry_on_db_lock()
async def save_draft(
    user_id: int, config: dict, *, name: str | None = None,
    draft_id: int | None = None,
) -> dict:
    """Сохраняет черновик → DRAFT_DICT.

    draft_id указан и принадлежит user_id → обновление config (и name, если
    передан: name=None — подпись не трогаем); иначе (id не передан, чужой id)
    — НОВЫЙ черновик. updated_at освежается при каждом сохранении.
    """
    async with get_sessionmaker()() as session:
        draft = None
        if draft_id is not None:
            found = await session.get(BuilderDraft, draft_id)
            if found is not None and found.user_id == int(user_id):
                draft = found
        if draft is None:
            draft = BuilderDraft(user_id=int(user_id))
            session.add(draft)
        draft.config = dict(config or {})
        if name is not None:
            draft.name = name
        draft.updated_at = datetime.now()  # гарантия освежения (JSON-конфиг мог совпасть)
        await session.commit()
        return _draft_dict(draft)


async def list_drafts(user_id: int) -> list[dict]:
    """Черновики пользователя (DRAFT_DICT), свежие первыми."""
    async with get_sessionmaker()() as session:
        rows = await session.scalars(
            select(BuilderDraft)
            .where(BuilderDraft.user_id == int(user_id))
            .order_by(BuilderDraft.updated_at.desc(), BuilderDraft.id.desc())
        )
        return [_draft_dict(d) for d in rows]


async def get_draft(draft_id: int, user_id: int) -> dict | None:
    """Черновик пользователя (DRAFT_DICT); чужой или несуществующий → None."""
    async with get_sessionmaker()() as session:
        draft = await session.get(BuilderDraft, draft_id)
    if draft is None or draft.user_id != int(user_id):
        return None
    return _draft_dict(draft)


@retry_on_db_lock()
async def delete_draft(draft_id: int, user_id: int) -> bool:
    """Удаляет черновик пользователя; False — нет такого или чужой."""
    async with get_sessionmaker()() as session:
        draft = await session.get(BuilderDraft, draft_id)
        if draft is None or draft.user_id != int(user_id):
            return False
        await session.delete(draft)
        await session.commit()
        return True


# ============================================================
# recipes: производственные конфигурации (ЭТАП 10)
# ============================================================
def _recipe_slots(value, title: str) -> list[dict]:
    """Нормализация slots из БД → list[dict] (хотфикс Этапа 10а, Д2).

    Колонка recipes.slots — JSON: ORM обычно сам десериализует. Но если в
    ячейку записан JSON-текст строки (двойное кодирование) или список
    JSON-строк, наружу уходит str/список строк — и итерация в
    services.builder.recipes даёт символы → AttributeError: 'str' object
    has no attribute 'get' (боевой краш Этапа 10а). Фасад — единственная
    точка чтения рецептов — приводит ЛЮБУЮ сохранённую форму к list[dict]:
    верхнеуровневая str → json.loads; элемент-строка → json.loads; элемент-
    не-dict после этого → ValueError с названием рецепта (диагностика
    вместо падения в глубине сервисов).
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError(
                f"recipes: битая форма slots у рецепта {title!r} — текст не JSON"
            ) from None
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(
            f"recipes: битая форма slots у рецепта {title!r} — "
            f"ожидается список, получен {type(value).__name__}"
        )
    slots: list[dict] = []
    for slot in value:
        if isinstance(slot, str):
            try:
                slot = json.loads(slot)
            except ValueError:
                raise ValueError(
                    f"recipes: битая форма slots у рецепта {title!r} — "
                    "элемент-строка не JSON"
                ) from None
        if not isinstance(slot, dict):
            raise ValueError(
                f"recipes: битая форма slots у рецепта {title!r} — элемент "
                f"{type(slot).__name__} вместо слота"
            )
        slots.append(slot)
    return slots


def _recipe_dict(recipe: Recipe) -> dict:
    """Строка recipes → RECIPE_DICT (slots всегда list[dict] — Д2/Этап 10а)."""
    return {
        "id": recipe.id,
        "size": recipe.size,
        "title": recipe.title,
        "slots": _recipe_slots(recipe.slots, str(recipe.title or "?")),
        "is_active": recipe.is_active,
        "created_at": _iso(recipe.created_at),
    }


async def list_recipes(size: int | None = None, active_only: bool = True) -> list[dict]:
    """Активные рецепты (RECIPE_DICT); опционально фильтр по размеру.

    Сортировка: size ↑, затем id (порядок сида = порядок владельца).
    """
    async with get_sessionmaker()() as session:
        stmt = select(Recipe).order_by(Recipe.size, Recipe.id)
        if size is not None:
            stmt = stmt.where(Recipe.size == int(size))
        if active_only:
            stmt = stmt.where(Recipe.is_active.is_(True))
        rows = (await session.execute(stmt)).scalars().all()
        return [_recipe_dict(r) for r in rows]


async def get_recipe(recipe_id: int) -> dict | None:
    """RECIPE_DICT одного рецепта (или None — не найден/не активен НЕ проверяется)."""
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(select(Recipe).where(Recipe.id == int(recipe_id)))
        ).scalar_one_or_none()
        return _recipe_dict(row) if row is not None else None


# Хотфикс Этапа 10б: в заголовки шаблонных конфигураций добавлен префикс
# «GEPRC». Сид идемпотентен по (size, title) — смена title в RECIPES создала
# бы дубли вместо правки существующих, поэтому строки БД со старым заголовком
# переименовываются на месте (id, слоты и правки владельца сохраняются).
# Если дубль с новым заголовком уже создан (бот перезапускался после
# переименования), остаётся строка с меньшим id, дубликат удаляется.
_RECIPE_TITLE_RENAMES: dict[str, str] = {
    "Taker 65A Stack · BrotherHobby": "GEPRC Taker 65A Stack · BrotherHobby",
    "Taker 65A · Рагнарек": "GEPRC Taker 65A · Рагнарек",
}


@retry_on_db_lock()
async def seed_recipes(session) -> dict:
    """Сид производственных рецептов (data.builder.recipes.RECIPES). Идемпотентен.

    Идемпотентность — по уникальной паре (size, title): существующий рецепт
    не пересоздаётся и не перезаписывается (правки владельца — через БД,
    сид не затирает ручные изменения). Исключение — ремонт формы slots
    (хотфикс Этапа 10а): если в БД слоты сохранились битыми (строка JSON /
    список строк — источник боевого AttributeError: 'str' object has no
    attribute 'get'), они перезаписываются из эталона RECIPES (part_key
    резолвится заново), счётчик попадает в отчёт "repaired". part_key слота
    резолвится из parts по нормализованному имени на момент сида;
    ненайденное имя НЕ выдумывается — слот сидируется с part_key=None и
    попадает в отчёт unresolved.

    Хотфикс Этапа 10б: заголовки шаблонов переименованы в RECIPES (префикс
    «GEPRC»). Чтобы смена title не создала дубликат, строки БД со старым
    заголовком (_RECIPE_TITLE_RENAMES) переименовываются на месте — id,
    слоты и правки владельца сохраняются (счётчик "renamed"). Если дубль
    с новым заголовком уже создан, остаётся строка с меньшим id, дубликат
    удаляется.

    Возвращает {"created": int, "repaired": int, "renamed": int,
                "unresolved": [(title, category, name), ...]}.
    """  # noqa: E501
    from data.builder.recipes import RECIPES  # локальный импорт: data ← db по архитектуре

    created = 0
    repaired = 0
    renamed = 0
    unresolved: list[tuple[str, str, str]] = []
    async with session.begin_nested():
        existing_rows = (await session.execute(select(Recipe))).scalars().all()
        # Хотфикс 10б: переименование старых заголовков в новые — ДО сверки
        # existing, иначе смена title в RECIPES создала бы дубликат вместо
        # правки существующей строки. Коллизия с UNIQUE(size, title): если
        # дубль уже создан, остаётся более старая строка (меньший id),
        # дубликат удаляется.
        deferred: list[tuple[Recipe, str]] = []
        for row in list(existing_rows):
            new_title = _RECIPE_TITLE_RENAMES.get(row.title)
            if new_title is None:
                continue
            twin = next(
                (
                    r
                    for r in existing_rows
                    if r.size == row.size and r.title == new_title
                ),
                None,
            )
            if twin is None:
                row.title = new_title
                renamed += 1
                continue
            keep, drop = (row, twin) if row.id < twin.id else (twin, row)
            await session.delete(drop)
            existing_rows.remove(drop)
            renamed += 1
            if keep.title != new_title:
                deferred.append((keep, new_title))
        if deferred:
            # flush: DELETE дублей обязан выполниться ДО UPDATE заголовка —
            # SQLAlchemy пишет updates раньше deletes, и UPDATE упёрся бы в
            # UNIQUE(size, title) ещё не удалённой строки-дубликата.
            await session.flush()
            for keep, new_title in deferred:
                keep.title = new_title
        existing = {(r.size, r.title) for r in existing_rows}
        names: dict[str, str] = dict(
            (await session.execute(select(Part.key, Part.name))).all()
        )

        def _spec_slots(spec: dict) -> list[dict]:
            """Слоты эталонного спека → JSON-форма БД (резолв part_key)."""
            out: list[dict] = []
            for category, slot in spec["slots"].items():
                if slot is None:
                    continue  # слот не используется в этой конфигурации
                name, qty = slot
                key = normalize_part_key(name)
                part_key = key if key in names else None
                if part_key is None:
                    unresolved.append((spec["title"], category, name))
                out.append(
                    {"category": category, "part_key": part_key, "name": name, "qty": qty}
                )
            return out

        for spec in RECIPES:
            if (spec["size"], spec["title"]) in existing:
                # Дозаполнение (Этап 10) + ремонт формы (Этап 10а): если
                # рецепт сидировался до импорта остатков (parts был пуст),
                # его слоты остались с part_key=None — повторный сид
                # резолвит ТОЛЬКО None-слоты по текущему справочнику;
                # не-None ключи (успешный резолв и правки владельца) не
                # перезаписываются. Битая форма slots (строка/список строк —
                # боевая форма краша Этапа 10а) — единственное исключение:
                # перезаписывается из эталона целиком.
                recipe = next(
                    r
                    for r in existing_rows
                    if r.size == spec["size"] and r.title == spec["title"]
                )
                if not (
                    isinstance(recipe.slots, list)
                    and all(isinstance(s, dict) for s in recipe.slots)
                ):
                    recipe.slots = _spec_slots(spec)
                    repaired += 1
                    continue
                slots_json = [dict(s) for s in (recipe.slots or [])]
                changed = False
                for slot in slots_json:
                    if slot.get("part_key") is None:
                        key = normalize_part_key(str(slot.get("name") or ""))
                        if key in names:
                            slot["part_key"] = key
                            changed = True
                        else:
                            unresolved.append(
                                (spec["title"], slot.get("category"), slot.get("name"))
                            )
                if changed:
                    recipe.slots = slots_json
                continue
            slots_json = _spec_slots(spec)
            session.add(
                Recipe(size=spec["size"], title=spec["title"], slots=slots_json)
            )
            created += 1
    await session.commit()
    return {
        "created": created,
        "repaired": repaired,
        "renamed": renamed,
        "unresolved": unresolved,
    }



