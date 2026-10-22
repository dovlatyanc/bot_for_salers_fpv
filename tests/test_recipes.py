"""Рецепты и модель совместимости (ЭТАП 10).

Ч��стые слои services.builder.recipes: L1 whitelist (allowed_union +
whitelist_errors — ТОЛЬКО категории модели "recipe"), L2 рецептурный тип
FC+ESC (fc_esc_shape_errors), L4 наличие (recipe_builds по слотам рецепта;
drone_consumption — расход на дрон для review) + universal_note. Модель
совместимости COMPAT_MODEL/model_of (data.builder.recipes): камеры —
"universal" (подходят любому дрону), рама — "size" (выбирается по размеру,
в рецептах её нет вовсе), vtx/rx/антенны — "freq" (ограничение по частотам,
L3). Сид seed_recipes (db.repositories_builder) — на изолированной БД
conftest: init_db сидирует рецепты ДО импорта остатков, поэтому слоты
сначала part_key=None; повторный сид бэкфиллит ТОЛЬКО None-ключи
(идемпотентность по паре (size, title)).
"""

import pytest

import db
from data.builder.recipes import COMPAT_MODEL, RECIPES, model_of
from db.models import Part, Recipe
from db.repositories import list_recipes, seed_recipes
from db.stock_import import part_key as normalize_part_key
from services.builder.recipes import (
    allowed_union,
    drone_consumption,
    fc_esc_shape_errors,
    recipe_builds,
    recipe_motor_qty,
    universal_note,
    whitelist_errors,
)
from sqlalchemy import delete, select

# Нормализованные ключи моторов трёх размеров (имена 1С — как в RECIPES)
MOTOR_4214 = normalize_part_key("Мотор axisfly 4214 380KV")
MOTOR_4320 = normalize_part_key("Мотор axisfly 4320 380KV")
MOTOR_5315 = normalize_part_key("Мотор axisfly 5315 380KV")


def _shaped() -> list[dict]:
    """RECIPES → форма БД (slots — список {category, part_key, name, qty})."""
    out = []
    for spec in RECIPES:
        slots = [
            {"category": category, "part_key": normalize_part_key(name),
             "name": name, "qty": qty}
            for category, slot in spec["slots"].items() if slot is not None
            for name, qty in (slot,)
        ]
        out.append({"size": spec["size"], "title": spec["title"],
                    "slots": slots, "is_active": True})
    return out


def _all_names() -> dict[str, str]:
    """{нормализованный ключ: имя 1С} всех используемых слотов RECIPES."""
    names: dict[str, str] = {}
    for spec in RECIPES:
        for slot in spec["slots"].values():
            if slot is not None:
                name, _qty = slot
                names[normalize_part_key(name)] = name
    return names


async def _clear_recipes(sessionmaker) -> None:
    async with sessionmaker() as session:
        await session.execute(delete(Recipe))
        await session.commit()


# ============================================================
# L1: allowed_union — whitelist деталей по размерам
# ============================================================

def test_union_13_motors_only_4214_and_4320():
    """13″ моторы — ровно {4214, 4320}; 5315 и 4320 (гекса) живут в 15″."""
    union = allowed_union(_shaped())
    assert union[13]["motor"] == {MOTOR_4214, MOTOR_4320}
    assert MOTOR_5315 not in union[13]["motor"]
    assert union[15]["motor"] == {MOTOR_5315, MOTOR_4320}
    assert MOTOR_4214 not in union[15]["motor"]


def test_union_10_fc_esc_stack_is_4():
    """10″ стеки: 4 различных part_key (по числу стеков в RECIPES)."""
    union = allowed_union(_shaped())
    assert len(union[10]["fc_esc_stack"]) == 4
    # 13″ собирается раздельными FC/ESC — стека в union нет
    assert "fc_esc_stack" not in union[13]
    assert union[13]["fc"] and union[13]["esc"]


def test_union_skips_unresolved_and_inactive():
    """part_key=None (неразрешённое имя) и is_active=False в union не попадают."""
    recipes = [
        {"size": 10, "slots": [{"category": "motor", "part_key": "m1", "qty": 4}],
         "is_active": True},
        {"size": 10, "slots": [{"category": "motor", "part_key": None,
                                "name": "Неразрешённое имя", "qty": 4}],
         "is_active": True},
        {"size": 10, "slots": [{"category": "motor", "part_key": "m2", "qty": 4}],
         "is_active": False},
    ]
    union = allowed_union(recipes)
    assert union[10]["motor"] == {"m1"}


def test_union_contains_only_recipe_model_categories():
    """В union нет категорий моделей "freq"/"universal"/"size": камер, рам,
    антенн, приёмников — whitelist образуют только "recipe"-слоты."""
    union = allowed_union(_shaped())
    for size_union in union.values():
        assert set(size_union) <= COMPAT_MODEL["recipe"]
        for forbidden in ("frame", "camera", "vtx", "rx", "antenna_air", "tx", "vrx"):
            assert forbidden not in size_union


# ============================================================
# Этап 11в: whitelist по паре (размер, число моторов)
# ============================================================

def test_union_motor_count_15_quad_and_hexa():
    """allowed_union(size, motor_count): квадро 15″ — только 5315, гекса 15″
    (6 моторов) — 4320; 13″ без изменений; None — прежнее поведение."""
    union = allowed_union(_shaped())
    assert MOTOR_4320 in union[15]["motor"] and MOTOR_5315 in union[15]["motor"]

    quad = allowed_union(_shaped(), motor_count=4)
    assert quad[15]["motor"] == {MOTOR_5315}
    assert MOTOR_4320 not in quad[15]["motor"]

    hexa = allowed_union(_shaped(), motor_count=6)
    assert MOTOR_4320 in hexa[15]["motor"]
    assert MOTOR_5315 not in hexa[15]["motor"]
    # ESC гексы (qty 2) в union(15, 6) есть; 13″ — все квадро, без изменений
    assert hexa[15]["esc"]
    assert quad[13]["motor"] == union[13]["motor"] == {MOTOR_4214, MOTOR_4320}


def test_recipe_motor_qty_both_slot_shapes():
    """recipe_motor_qty: эталонная форма slots (dict) и форма БД (список)."""
    recipes = _shaped()
    hexa = next(r for r in recipes if r["title"] == "HEXA 4320 Hexacopter")
    assert recipe_motor_qty(hexa) == 6
    quad15 = next(r for r in recipes if r["size"] == 15 and r["title"] != hexa["title"])
    assert recipe_motor_qty(quad15) == 4
    # эталонная форма {category: (name, qty)} и пустой слот motor → дефолт 4
    assert recipe_motor_qty({"slots": {"motor": ("Мотор axisfly 4320 380KV", 6)}}) == 6
    assert recipe_motor_qty({"slots": []}) == 4


def test_drone_consumption_free_mode_six_motors():
    """C3: расход свободного режима — motor = motor_count (6), остальные ×1;
    рецепты: qty слота не меньше motor_count; проп — 1 комплект (без ceil)."""
    config = {"slots": {"motor": {"key": MOTOR_4320, "qty": 1}}}
    assert drone_consumption(config, {}, motor_count=6) == {"motor": 6}
    assert drone_consumption(config, {}) == {"motor": 4}
    # гекса-рецепт: мотор ×6, ESC ×2, проп 1 комплект
    hexa_config = {
        "slots": {
            "motor": {"key": MOTOR_4320, "qty": 6},
            "prop": {"key": "p", "qty": 1},
            "esc": {"key": "e", "qty": 2},
        }
    }
    assert drone_consumption(hexa_config, {}) == {"motor": 6, "prop": 1, "esc": 2}


def test_recipe_builds_hexa_uses_recipe_qty():
    """recipe_builds считает по собственным qty рецепта (мотор ×6, ESC ×2);
    mto-слот рамы в рецепте отсутствует — на расчёт не влияет."""
    hexa_slots = [
        {"category": "motor", "part_key": "m", "name": "М", "qty": 6},
        {"category": "prop", "part_key": "p", "name": "П", "qty": 1},
        {"category": "esc", "part_key": "e", "name": "E", "qty": 2},
    ]
    stock = {"m": 12.0, "p": 5.0, "e": 4.0}
    assert recipe_builds(hexa_slots, stock) == 2  # min(12//6, 5//1, 4//2)


# ============================================================
# L1: whitelist_errors — только "recipe"; universal/size/freq молчат
# ============================================================

def test_whitelist_errors_rejects_motor_outside_serial_builds():
    """Мотор 5315 на 13″ — вне серийных сборок → ошибка L1 (на 15″ — молчит)."""
    union = allowed_union(_shaped())
    config = {"slots": {"motor": {"key": MOTOR_5315, "qty": 4}}}
    errors = whitelist_errors(config, 13, union)
    assert len(errors) == 1
    assert "не применяется в серийных сборках 13″" in errors[0]
    assert MOTOR_5315 in errors[0]
    assert whitelist_errors(config, 15, union) == []
    assert whitelist_errors({"slots": {}}, 10, union) == []


def test_whitelist_errors_ignores_universal_size_freq_slots():
    """Камеры (universal), рама (size), vtx/tx (freq) L1 не образуют:
    тепловизор и дневная камера проходят на 10/13/15″ без ошибок L1."""
    union = allowed_union(_shaped())
    for camera_key in ("камера тепловизионная fpv 640", "камера дневная fw"):
        config = {"slots": {
            "camera": {"key": camera_key, "qty": 1},
            "battery": None,                     # пустой слот — пропуск
            "frame": {"key": "рама 13 дюймов", "qty": 1,
                      "name": "Рама 13 дюймов"},
            "vtx": {"key": "vtx 1.3g", "qty": 1, "name": "VTX 1.3G"},
            "tx": {"key": "передатчик произвольный", "qty": 1},
        }}
        for size in (10, 13, 15):
            assert whitelist_errors(config, size, union) == []


# ============================================================
# L2: fc_esc_shape_errors — стек vs раздельные FC/ESC
# ============================================================

@pytest.mark.parametrize(("size", "slots", "has_error"), [
    (10, {"fc_esc_stack": {"key": "стек"}}, False),
    (10, {"fc": {"key": "fc"}}, True),
    (10, {"esc": {"key": "esc"}}, True),
    (13, {"fc": {"key": "fc"}, "esc": {"key": "esc"}}, False),
    (13, {"fc_esc_stack": {"key": "стек"}}, True),
    (15, {"fc_esc_stack": {"key": "стек"}, "fc": {"key": "fc"}}, False),
])
def test_fc_esc_shape_errors_by_size(size, slots, has_error):
    errors = fc_esc_shape_errors({"slots": slots}, size)
    assert bool(errors) is has_error
    if has_error:
        assert "собирается" in errors[0] and "″" in errors[0]


# ============================================================
# L4: recipe_builds — сколько дронов собирается из остатков (по слотам)
# ============================================================

def test_recipe_builds_min_across_slots():
    slots = [
        {"category": "motor", "part_key": "m", "qty": 4},
        {"category": "prop", "part_key": "p", "qty": 1},
    ]
    assert recipe_builds(slots, {"m": 8, "p": 3}) == 2      # min(8//4, 3//1)
    assert recipe_builds(slots, {"m": 7, "p": 3}) == 1      # floor(7/4)
    assert recipe_builds(slots, {"m": 8}) == 0              # пропов нет
    assert recipe_builds(slots, {}) == 0


def test_recipe_builds_title_in_low_stock_warning():
    """Хотфикс Этапа 10а: title в recipe_builds — только diagnose-тег
    нормалайзера (битая форма slots), на число сборок не влияет; при
    part_key=None сборка обнуляется даже при большом остатке."""
    slots = [
        {"category": "motor", "part_key": "m", "qty": 4},
        {"category": "prop", "part_key": "p", "qty": 1},
    ]
    assert recipe_builds(slots, {"m": 3, "p": 3}, "ECO F405 10″") == 0
    assert recipe_builds(slots, {"m": 8, "p": 3}, "ECO F405 10″") == 2
    # diagnose-путь: слоты-не-список → ValueError с названием рецепта
    with pytest.raises(ValueError, match="ECO F405"):
        recipe_builds({"slots": "мусор"}, {"m": 8}, "ECO F405 10″")


def test_recipe_builds_stack_limited_114():
    """Матрица владельца: 114 = остаток стека, не рам — рамы в рецептах нет
    (модель "size"), поэтому рама не ограничивает собираемость."""
    slots = [
        {"category": "motor", "part_key": "m", "qty": 4},
        {"category": "fc_esc_stack", "part_key": "s", "qty": 1},
    ]
    assert recipe_builds(slots, {"m": 1000, "s": 114}) == 114


def test_recipe_builds_unresolved_slot_blocks_build():
    """Слот с part_key=None (имя не в каталоге) обнуляет сборку."""
    slots = [{"category": "motor", "part_key": None, "qty": 4}]
    assert recipe_builds(slots, {"m": 100}) == 0
    assert recipe_builds([], {"m": 100}) == 0               # пустой рецепт


# ============================================================
# L4 в review: drone_consumption — расход на дрон
# ============================================================

def test_drone_consumption_motor_four_rest_one():
    """motor ×4, остальные слоты ×1; пустой конфиг → пустой расход.

    Расход «антенна ×0 при VTX-комплекте» упразднён вместе со слотом
    antenna_air (хотфикс Этапа 10б); легаси-слот старого черновика
    считается ×1 как обычная деталь.
    """
    config = {"slots": {
        "motor": {"key": "m", "qty": 1},
        "prop": {"key": "p", "qty": 1},
        "camera": {"key": "c", "qty": 1},
        "antenna_air": {"key": "a", "qty": 1},   # легаси-слот старого черновика
    }}
    plain = {"vtx": {"name": "Видеопередатчик Axisflying 3W 5.8ГГц комплект"}}
    assert drone_consumption(config, plain) == {
        "motor": 4, "prop": 1, "camera": 1, "antenna_air": 1,
    }
    assert drone_consumption({"slots": {}}, plain) == {}

def test_drone_consumption_recipe_qty_overrides_default():
    """Этап 10в: явный qty слота больше дефолта учитывается (гекса: мотор
    ×6, ESC ×2); мотор с qty=1 (свободный режим) остаётся ×4."""
    config = {"slots": {
        "motor": {"key": "m", "qty": 6},
        "esc": {"key": "e", "qty": 2},
        "prop": {"key": "p", "qty": 1},
    }}
    assert drone_consumption(config, {}) == {"motor": 6, "esc": 2, "prop": 1}
    config_free = {"slots": {"motor": {"key": "m", "qty": 1}}}
    assert drone_consumption(config_free, {}) == {"motor": 4}


# ============================================================
# Модель совместимости: model_of / universal_note
# ============================================================

def test_model_of_groups():
    assert model_of("motor") == "recipe"
    assert model_of("prop") == "recipe"
    assert model_of("fc_esc_stack") == "recipe"
    assert model_of("fc") == "recipe"
    assert model_of("esc") == "recipe"
    assert model_of("frame") == "size"
    assert model_of("spare") == "size"
    assert model_of("vtx") == "freq"
    assert model_of("vrx") == "freq"
    assert model_of("antenna_air") == "freq"
    assert model_of("rx") == "freq"
    assert model_of("tx") == "freq"
    assert model_of("camera") == "universal"
    assert model_of("battery") == "universal"
    assert model_of("payload") == "universal"
    assert model_of("нет такой категории") == "universal"   # неизвестная


def test_universal_note():
    assert universal_note("camera") == "подходит к любому дрону"
    assert universal_note("motor") is None
    assert universal_note("frame") is None
    assert universal_note("vtx") is None


# ============================================================
# Сид seed_recipes: идемпотентность и бэкфилл None-ключей
# ============================================================

async def test_seed_recipes_idempotent_on_clean_table():
    """Повторный сид ничего не создаёт (идемпотентность по (size, title))."""
    sessionmaker = db.get_sessionmaker()
    await _clear_recipes(sessionmaker)
    async with sessionmaker() as session:
        report = await seed_recipes(session)
        assert report["created"] == 13
        assert report["unresolved"]              # parts пуст → все слоты None
    async with sessionmaker() as session:
        assert (await seed_recipes(session))["created"] == 0
    assert len(await list_recipes(active_only=False)) == 13


async def test_seed_recipes_backfills_none_keys_after_parts_appear():
    """Повторный сид после появления parts дозаполняет None-ключи."""
    sessionmaker = db.get_sessionmaker()
    await _clear_recipes(sessionmaker)
    async with sessionmaker() as session:
        assert (await seed_recipes(session))["created"] == 13
    async with sessionmaker() as session:
        for key, name in _all_names().items():
            session.add(Part(key=key, name=name, unit="шт",
                             category="other", params={}))
        await session.commit()
    async with sessionmaker() as session:
        report = await seed_recipes(session)
        assert report["created"] == 0
        assert report["unresolved"] == []        # всё разрешилось
    for recipe in await list_recipes(active_only=False):
        assert recipe["slots"], recipe["title"]
        assert all(s["part_key"] for s in recipe["slots"]), recipe["title"]


async def test_seed_recipes_backfill_touches_only_none_slots():
    """Бэкфилл чинит только part_key=None; успешный резолв/правки не трогаются."""
    sessionmaker = db.get_sessionmaker()
    await _clear_recipes(sessionmaker)
    rx_name = "Приемник (RX) 915MHz"
    motor_name = "Мотор для FPV BrotherHobby 3115 900 KV SE"
    async with sessionmaker() as session:
        session.add(Part(key=normalize_part_key(rx_name), name=rx_name,
                         unit="шт", category="rx", params={}))
        await session.commit()
    async with sessionmaker() as session:
        await seed_recipes(session)
    # правка владельца в БД: подменяем успешный ключ RX в первом 10″-рецепте.
    # Ловушка JSON-колонки: мутировать dict in-place нельзя — SQLAlchemy не
    # заметит изменения; строим НОВЫЕ dict-объекты, тогда UPDATE выполнится.
    async with sessionmaker() as session:
        row = ((await session.execute(
            select(Recipe).where(Recipe.size == 10).order_by(Recipe.id)
        )).scalars().first())
        row.slots = [
            {**s, "part_key": "ручная правка владельца"}
            if s["category"] == "rx" else dict(s)
            for s in row.slots
        ]
        await session.commit()
    async with sessionmaker() as session:
        session.add(Part(key=normalize_part_key(motor_name), name=motor_name,
                         unit="шт", category="motor", params={}))
        await session.commit()
    async with sessionmaker() as session:
        assert (await seed_recipes(session))["created"] == 0
    async with sessionmaker() as session:
        row = ((await session.execute(
            select(Recipe).where(Recipe.size == 10).order_by(Recipe.id)
        )).scalars().first())
        rx_slot = next(s for s in row.slots if s["category"] == "rx")
        assert rx_slot["part_key"] == "ручная правка владельца"   # не перезаписан
        motor_slot = next(s for s in row.slots if s["category"] == "motor")
        assert motor_slot["part_key"] == normalize_part_key(motor_name)  # дозаполнен


# ============================================================
# Хотфикс 10б: переименование заголовков шаблонов на месте
# ============================================================

# {новый заголовок (как в RECIPES): старый (как в БД до хотфикса)}
_GEPRC_RENAMES = {
    "GEPRC Taker 65A Stack · BrotherHobby": "Taker 65A Stack · BrotherHobby",
    "GEPRC Taker 65A · Рагнарек": "Taker 65A · Рагнарек",
}


async def test_seed_recipes_renames_old_titles_in_place():
    """Старые заголовки → новые на месте: дублей нет, правки владельца живы."""
    sessionmaker = db.get_sessionmaker()
    await _clear_recipes(sessionmaker)
    async with sessionmaker() as session:
        assert (await seed_recipes(session))["created"] == 13
    # имитация БД до хотфикса: заголовки ещё без префикса GEPRC
    async with sessionmaker() as session:
        rows = ((await session.execute(select(Recipe))).scalars().all())
        for row in rows:
            if row.title in _GEPRC_RENAMES:          # ключи — новые заголовки
                row.title = _GEPRC_RENAMES[row.title]
                row.slots = [
                    {**s, "part_key": "ручная правка владельца"}
                    if s["category"] == "rx" else dict(s)
                    for s in row.slots
                ]
        await session.commit()
    async with sessionmaker() as session:
        report = await seed_recipes(session)
        assert report["created"] == 0
        assert report["renamed"] == 2
    recipes = await list_recipes(active_only=False)
    assert len(recipes) == 13                        # дубликаты не созданы
    titles = {r["title"] for r in recipes}
    assert set(_GEPRC_RENAMES) <= titles             # новые заголовки в БД
    assert titles.isdisjoint(_GEPRC_RENAMES.values())
    # правка владельца пережила переименование — строка та же (id не менялся)
    async with sessionmaker() as session:
        rows = ((await session.execute(
            select(Recipe).where(
                Recipe.title == "GEPRC Taker 65A Stack · BrotherHobby"
            )
        )).scalars().all())
        assert len(rows) == 1
        rx_slot = next(s for s in rows[0].slots if s["category"] == "rx")
        assert rx_slot["part_key"] == "ручная правка владельца"


async def test_seed_recipes_dedupes_stale_title_duplicates():
    """Дубль с новым заголовком уже создан: остаётся старая строка (правки
    владельца), дубликат удаляется — UniqueConstraint(size, title) не рвётся."""
    sessionmaker = db.get_sessionmaker()
    await _clear_recipes(sessionmaker)
    async with sessionmaker() as session:
        assert (await seed_recipes(session))["created"] == 13
    # имитация БД, где бот уже перезапускался после переименования:
    # у двух заголовков есть и старая строка, и свежий дубликат-сид
    async with sessionmaker() as session:
        rows = ((await session.execute(select(Recipe))).scalars().all())
        for row in rows:
            new_title = row.title
            if new_title not in _GEPRC_RENAMES:
                continue
            row.title = _GEPRC_RENAMES[new_title]    # старый заголовок
            row.slots = [
                {**s, "part_key": "ручная правка владельца"}
                if s["category"] == "rx" else dict(s)
                for s in row.slots
            ]
            session.add(Recipe(size=row.size, title=new_title, slots=[]))
        await session.commit()
    async with sessionmaker() as session:
        report = await seed_recipes(session)
        assert report["created"] == 0
        assert report["renamed"] == 2
    assert len(await list_recipes(active_only=False)) == 13
    # выжила строка с правкой владельца (меньший id), дубликат удалён
    async with sessionmaker() as session:
        rows = ((await session.execute(
            select(Recipe).where(Recipe.title.in_(list(_GEPRC_RENAMES)))
        )).scalars().all())
        assert len(rows) == 2
        for row in rows:
            rx_slot = next(s for s in row.slots if s["category"] == "rx")
            assert rx_slot["part_key"] == "ручная правка владельца"

