"""Справочники data/builder (ЭТАП 2): чистые юнит-тесты, без БД.

Классификация REAL_EXAMPLES (реальные имена из боевой базы) прогоняется через
match_rule() с учётом ПОРЯДКА CLASSIFICATION_RULES: каждое имя должно попасть
в категорию своего правила, и ни одно правило ВЫШЕ по списку не должно
совпасть — иначе имя ушло бы не в ту категорию.
"""

from collections import Counter

import pytest

from data.builder.categories import BUILD_SLOTS, CATEGORY_LABELS
from data.builder.control_freq import freqs_intersect, parse_control_freq
from data.builder.drone_sizes import (
    DRONE_SIZES,
    MOTOR_STATOR_BY_SIZE,
    SIZE_TOKENS,
    detect_size,
    extract_stator,
)
from data.builder.electrical import (
    KV_RANGE_BY_SIZE,
    MIN_ESC_CURRENT_BY_SIZE,
    extract_current,
    extract_kv,
)
from data.builder.frequencies import bands_intersect, norm_num, parse_video_band
from data.builder.keywords import CLASSIFICATION_RULES, REAL_EXAMPLES, match_rule
from data.builder.recipes import COMPAT_MODEL, RECIPES, model_of
from db.models import PART_CATEGORIES


def first_match(name: str) -> str:
    """Первая совпавшая категория по порядку правил (фолбэк — «other»)."""
    for rule in CLASSIFICATION_RULES:
        if match_rule(name, rule):
            return rule["category"]
    return "other"


# ============================================================
# PART_CATEGORIES (Этап 2: 20 категорий)
# ============================================================

def test_part_categories_is_20():
    assert len(PART_CATEGORIES) == 20


def test_part_categories_include_new_four():
    for cat in ("battery", "fiber", "payload", "spare"):
        assert cat in PART_CATEGORIES


def test_part_categories_no_duplicates():
    assert len(PART_CATEGORIES) == len(set(PART_CATEGORIES))


def test_part_categories_order_is_stable():
    assert PART_CATEGORIES[:13] == (
        "frame", "motor", "prop", "fc_esc_stack", "fc", "esc", "vtx", "camera",
        "antenna_air", "antenna_ground", "rx", "tx", "vrx",
    )


# ============================================================
# CLASSIFICATION_RULES: ссылки на категории, ключи, порядок
# ============================================================

def test_rules_reference_known_categories():
    for rule in CLASSIFICATION_RULES:
        assert rule["category"] in PART_CATEGORIES


def test_rules_do_not_describe_fallback_other():
    assert "other" not in {r["category"] for r in CLASSIFICATION_RULES}


def test_rules_have_match_keys():
    for rule in CLASSIFICATION_RULES:
        assert "contains_any" in rule or "contains_all" in rule
        for key in ("contains_any", "excludes"):
            if key in rule:
                assert isinstance(rule[key], list) and rule[key]
        if "contains_all" in rule:
            assert isinstance(rule["contains_all"], list) and rule["contains_all"]
            assert all(isinstance(g, list) and g for g in rule["contains_all"])


def test_rules_are_unique_by_category():
    cats = [r["category"] for r in CLASSIFICATION_RULES]
    assert len(cats) == len(set(cats))


def test_priority_fiber_before_rx_and_vrx():
    idx = {r["category"]: i for i, r in enumerate(CLASSIFICATION_RULES)}
    assert idx["fiber"] < idx["vrx"]
    assert idx["fiber"] < idx["rx"]


def test_priority_battery_before_finished():
    idx = {r["category"]: i for i, r in enumerate(CLASSIFICATION_RULES)}
    assert idx["battery"] < idx["finished"]


def test_priority_fc_esc_stack_before_fc_and_esc():
    idx = {r["category"]: i for i, r in enumerate(CLASSIFICATION_RULES)}
    assert idx["fc_esc_stack"] < idx["fc"]
    assert idx["fc_esc_stack"] < idx["esc"]


def test_priority_vtx_before_tx_and_camera():
    idx = {r["category"]: i for i, r in enumerate(CLASSIFICATION_RULES)}
    assert idx["vtx"] < idx["tx"]
    assert idx["vtx"] < idx["camera"]


def test_priority_vrx_before_rx_and_antennas():
    idx = {r["category"]: i for i, r in enumerate(CLASSIFICATION_RULES)}
    assert idx["vrx"] < idx["rx"]
    assert idx["vrx"] < idx["antenna_air"]
    assert idx["vrx"] < idx["antenna_ground"]


def test_priority_spare_before_frame_and_antennas():
    idx = {r["category"]: i for i, r in enumerate(CLASSIFICATION_RULES)}
    assert idx["spare"] < idx["frame"]
    assert idx["spare"] < idx["antenna_air"]
    assert idx["spare"] < idx["antenna_ground"]


# ============================================================
# match_rule: содержит_any / содержит_all / excludes
# ============================================================

def test_match_rule_contains_any():
    rule = {"category": "motor", "contains_any": ["мотор", "двигатель"]}
    assert match_rule("Мотор axisfly 3115 900KV", rule)
    assert match_rule("Двигатель бесколлекторный", rule)
    assert not match_rule("Пропеллер HQProp 10X5X3", rule)


def test_match_rule_contains_all_any_group():
    rule = {
        "category": "fc_esc_stack",
        "contains_all": [["полетный контроллер", "регулятор"], ["стек", "fc"]],
    }
    # первая И-группа целиком
    assert match_rule("Полетный контроллер + регулятор оборотов", rule)
    # вторая И-группа целиком
    assert match_rule("Стек FC+ESC 30x30", rule)
    # только часть группы — не совпадение
    assert not match_rule("Полетный контроллер Axisflying ECO F722", rule)


def test_match_rule_excludes_blocks():
    rule = {"category": "payload", "contains_any": ["сервопривод"],
            "excludes": ["удлинитель"]}
    assert match_rule("Сервопривод DS3240, 40 кг*см", rule)
    assert not match_rule("Удлинитель сервопривода JR кабель 7,5см", rule)


# ============================================================
# REAL_EXAMPLES: попадание в свою категорию с учётом порядка правил
# ============================================================

def test_real_examples_hit_own_rule():
    for cat, names in REAL_EXAMPLES.items():
        for name in names:
            assert first_match(name) == cat, f"{name!r} → {first_match(name)}"


def test_real_examples_not_hit_by_higher_rules():
    """Правила ВЫШЕ собственного не должны совпадать (иначе неверный порядок)."""
    order = [r["category"] for r in CLASSIFICATION_RULES]
    for cat, names in REAL_EXAMPLES.items():
        own = order.index(cat)
        for name in names:
            higher = {order[i] for i in range(own) if match_rule(name, CLASSIFICATION_RULES[i])}
            assert not higher, f"{name!r}: перехвачено правилами {higher}"


def test_real_examples_cover_all_rule_categories():
    assert set(REAL_EXAMPLES) == {r["category"] for r in CLASSIFICATION_RULES}


def test_real_examples_names_nonempty():
    for cat, names in REAL_EXAMPLES.items():
        assert cat in PART_CATEGORIES
        assert names
        assert all(str(n).strip() for n in names)


# ============================================================
# BUILD_SLOTS и CATEGORY_LABELS
# ============================================================

def test_build_slots_order():
    """Хотфикс Этапа 10б: антенны/VRX/payload выведены из слотов борта — 7 слотов."""
    assert [cat for cat, _, _ in BUILD_SLOTS] == [
        "frame", "motor", "prop", "fc_esc_stack", "vtx", "camera", "rx",
    ]


def test_build_slots_required():
    """Хотфиксы 10а/10б: АКБ/TX/антенны/VRX/payload не слоты; все 7 обязательны."""
    required = {cat for cat, req, _ in BUILD_SLOTS if req}
    assert required == {
        "frame", "motor", "prop", "fc_esc_stack", "vtx", "camera", "rx",
    }
    optional = {cat for cat, req, _ in BUILD_SLOTS if not req}
    assert optional == set()


def test_build_slots_categories_and_titles():
    for cat, _req, title in BUILD_SLOTS:
        assert cat in PART_CATEGORIES
        assert title.strip()


def test_category_labels_cover_part_categories():
    assert set(CATEGORY_LABELS) == set(PART_CATEGORIES)
    assert all(label.strip() for label in CATEGORY_LABELS.values())


# ============================================================
# drone_sizes: размеры и статоры
# ============================================================

def test_drone_sizes_constant():
    assert DRONE_SIZES == {7, 9, 10, 13, 15}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Рама 10 МАРК4-V2", 10),
        ("Пропеллер HQProp 10Х5Х3 (2CW+2CCW)", 10),
        ("Пропеллер HQ 13?9?3 (2CW+2CCW)", 13),
        ("Пропеллеры HQ 15?7?3 (2CW+2CCW)", 15),
        ("Моторы 3115 рагнарек для 9 дюймов", 9),
        ("Рама карбон Mark4", 10),                     # хотфикс Д1: токен mark4 → 10″
    ],
)
def test_detect_size_by_tokens(name, expected):
    assert detect_size(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "Переходник SMA-female - MMCX угловой",
        "Удлинитель сервопривода  JR кабель 7,5см",   # 7,5 — не размер
        "Термоусадочная пленка из ПВХ Ширина 120 мм ? 0,12 мм (1 кг ? 22 м)",
        "Мотор axisfly 3115 900KV",                    # числа статора/KV не размер
    ],
)
def test_detect_size_unknown(name):
    assert detect_size(name) is None


def test_mark4_token_only_in_frame_names():
    """Хотфикс Д1: токен mark4/марк4 → 10″ добавлен в SIZE_TOKENS[10]; в боевой
    выгрузке токен встречается только у рам — ложно срабатывать на не-рамах
    (моторы/пропы/антенны) ему нельзя."""
    hits = [
        (category, name)
        for category, names in REAL_EXAMPLES.items()
        for name in names
        if "mark4" in name.lower() or "марк4" in name.lower()
    ]
    assert hits, "в REAL_EXAMPLES нет имён с токеном mark4/марк4"
    assert all(category == "frame" for category, _name in hits)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Мотор axisfly 4320 380KV", "4320"),
        ("Мотор для FPV BrotherHobby Tornado 3115-1050KV", "3115"),
        ("Моторы 3115 рагнарек для 9 дюймов", "3115"),
    ],
)
def test_extract_stator(name, expected):
    assert extract_stator(name) == expected


def test_extract_stator_missing():
    assert extract_stator("Рама карбон Mark4") is None


def test_motor_stator_by_size_consistent():
    assert set(MOTOR_STATOR_BY_SIZE) <= DRONE_SIZES
    assert 7 not in MOTOR_STATOR_BY_SIZE          # для 7" статоры не нормированы
    for stators in MOTOR_STATOR_BY_SIZE.values():
        assert stators
        assert all(len(s) == 4 and s.isdigit() for s in stators)
    # Этап 10в: 4320 — статор и 13″-квадрокоптеров, и гексакоптера 15″
    assert "4320" in MOTOR_STATOR_BY_SIZE[13]
    assert "4320" in MOTOR_STATOR_BY_SIZE[15]


# ============================================================
# frequencies: видеодиапазоны (ГГц)
# ============================================================

@pytest.mark.parametrize(
    ("s", "expected"),
    [
        ("4,85", 4.85),
        ("5.8", 5.8),
        ("1 589,000", 1589.0),
    ],
)
def test_norm_num(s, expected):
    assert norm_num(s) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("DMKR 7.2G 5W VTX", (7.2, 7.2)),
        ("Видеопередатчик Rush Solo max 6,1-7,2 G vtx 3Вт", (6.1, 7.2)),
        ("Патч антенна Maple Wireless 4.85G - 6.3G", (4.85, 6.3)),
        # при наличии и «1,2G», и диапазона МГц приоритет у явного G-токена
        ("Видеоприемник YSRC VRX-F4-L 1,2G 9CH 1080Mhz to 1360Mhz", (1.2, 1.2)),
        ("Антенна Pagoda.5,2 ГГц", (5.2, 5.2)),
        ("Видеопередатчик 4.9G/5.8G/6G 3 Вт + антенна", (4.9, 6.0)),
    ],
)
def test_parse_video_band(name, expected):
    assert parse_video_band(name) == expected


def test_parse_video_band_mhz_range_fallback():
    """Диапазон в МГц используется только когда G-токена нет."""
    assert parse_video_band("Видеоприемник YSRC VRX-F4-L 1080Mhz to 1360Mhz") == (
        1.08, 1.36,
    )


@pytest.mark.parametrize(
    "name",
    [
        "Передатчик управления (TX) 170 MHz, 100 км",
        "Приемник axisfly (RX) 400MHz, Модель Generic C3 LR1121 400RX",
        "Передатчик axisfly ELRS 450МГц",
        "Приемник управления ELRS 2-х канальный (300/1950МГц)",
    ],
)
def test_parse_video_band_control_context_is_none(name):
    assert parse_video_band(name) is None


@pytest.mark.parametrize(
    "name",
    [
        "Рама карбон Mark4",
        "Сервопривод DS3240, 40 кг*см, 270 градусов",
        "Пропеллер HQProp 10Х5Х3 (2CW+2CCW)",
        "Оптоволокно для ФПВ (10 км)",
    ],
)
def test_parse_video_band_unknown(name):
    assert parse_video_band(name) is None


# ============================================================
# frequencies, мини-фикс 4.0: «голое» ГГц в конце имени
# у видео-категорий («Всенаправленная антенна 5.8»)
# ============================================================

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Всенаправленная антенна 5.8", (5.8, 5.8)),
        ("Антенна 3.3", (3.3, 3.3)),
        ("Антенна foxeer 5.8", (5.8, 5.8)),
        ("Приемный модуль 5,8", (5.8, 5.8)),     # запятая-десятичная + маркер vrx
        ("Видеопередатчик 6,2", (6.2, 6.2)),
    ],
)
def test_parse_video_band_bare_ghz_fallback(name, expected):
    assert parse_video_band(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        # нет видеоконтекста — «голое» число видеодиапазоном не считается
        "Кабель с разъемом 30см, JST SH 1.0мм 7pin / разъем на плату JST SH 7pin / 5шт",
        "Сервопривод DS3240, 40 кг*см, 270 градусов",
        "Пластик для 3D принтера PET-G",
        "Рама карбон Mark4",
        # видеоконтекст есть, но число в конце не «голые ГГц»
        "Антенна 600/6000",            # не десятичное в конце
        "Антенна 12.5",                # вне диапазона 1.0–7.9
        "Антенна 915",                 # целое число — не десятичное
    ],
)
def test_parse_video_band_bare_ghz_negative(name):
    assert parse_video_band(name) is None


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((6.1, 7.2), (6.1, 7.2), True),
        ((5.8, 5.8), (6.1, 7.2), False),
        ((4.85, 6.3), (5.8, 5.8), True),
    ],
)
def test_bands_intersect(a, b, expected):
    assert bands_intersect(a, b) is expected


# ============================================================
# control_freq: частоты управления (МГц)
# ============================================================

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Передатчик управления (TX) 170 MHz, 100 км", (170.0, 170.0)),
        ("Приемник axisfly (RX) 400MHz, Модель Generic C3 LR1121 400RX", (400.0, 400.0)),
        ("Приемник управления ELRS 2-х канальный (300/1950МГц)", (300.0, 1950.0)),
        ("Приемник HappyModel 2,4G EP1 TCXO", (2400.0, 2400.0)),
        ("Четвертьволновая антенна 915", (915.0, 915.0)),
    ],
)
def test_parse_control_freq(name, expected):
    assert parse_control_freq(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "DMKR 7.2G 5W VTX",
        "Видеоприемник YSRC VRX-F4-L 1,2G 9CH 1080Mhz to 1360Mhz",
        "Рама карбон Mark4",
    ],
)
def test_parse_control_freq_unknown(name):
    assert parse_control_freq(name) is None


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((300.0, 1950.0), (915.0, 915.0), True),
        ((170.0, 170.0), (915.0, 915.0), False),
    ],
)
def test_freqs_intersect(a, b, expected):
    assert freqs_intersect(a, b) is expected


def test_video_and_control_parsers_split_domains():
    """Частота не должна попадать в чужой парсер."""
    assert parse_video_band("DMKR 7.2G 5W VTX") == (7.2, 7.2)
    assert parse_control_freq("DMKR 7.2G 5W VTX") is None
    assert parse_control_freq("Приемник управления (RX) 170 MHz, 100 км") == (170.0, 170.0)
    assert parse_video_band("Приемник управления (RX) 170 MHz, 100 км") is None


# ============================================================
# electrical: KV и ток (advisory)
# ============================================================

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Мотор axisfly 3115 900KV", 900),
        ("Мотор axisfly 4320 380KV", 380),
        ("Мотор для FPV BrotherHobby Tornado 3115-1050KV", 1050),
    ],
)
def test_extract_kv(name, expected):
    assert extract_kv(name) == expected


def test_extract_kv_cyrillic_not_matched():
    """Кириллическое «КВ» не ловится — ложные срабатывания."""
    assert extract_kv("Мотор 3115 900КВ") is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("регулятор оборотов Axisflying Argus ECO 100A", 100),
        ("Полетный контроллер + регулятор оборотов GEPRC TAKER F722 BLS 80A V2", 80),
        ("Регулятор оборотов Axisflying ECO 60A", 60),
    ],
)
def test_extract_current(name, expected):
    assert extract_current(name) == expected


def test_extract_current_missing():
    assert extract_current("Мотор axisfly 3115 900KV") is None


def test_electrical_constants_cover_drone_sizes():
    assert set(KV_RANGE_BY_SIZE) == DRONE_SIZES
    assert set(MIN_ESC_CURRENT_BY_SIZE) == DRONE_SIZES
    assert all(lo <= hi for lo, hi in KV_RANGE_BY_SIZE.values())
    assert all(cur > 0 for cur in MIN_ESC_CURRENT_BY_SIZE.values())


# ============================================================
# recipes: производственные рецепты (Этап 10)
# ============================================================

def test_recipes_count_and_sizes():
    """13 конфигураций владельца: 5×10″, 3×13″, 4×15″ + гексакоптер 15″."""
    by_size = Counter(recipe["size"] for recipe in RECIPES)
    assert len(RECIPES) == 13
    assert dict(by_size) == {10: 5, 13: 3, 15: 5}


def test_recipes_unique_by_size_and_title():
    """Ключ сида (size, title) уникален — повторный сид не дублирует."""
    pairs = [(recipe["size"], recipe["title"]) for recipe in RECIPES]
    assert len(pairs) == len(set(pairs))


def test_recipes_slots_valid_categories_and_qty():
    """Категории слотов валидны; расход на дрон: мотор ×4, остальное ×1."""
    for recipe in RECIPES:
        for category, slot in recipe["slots"].items():
            assert category in PART_CATEGORIES, (recipe["title"], category)
            if slot is None:                      # слот не используется
                continue
            name, qty = slot
            assert isinstance(name, str) and name.strip()
            # Гексакоптер (Этап 10в): 6 моторов и два ESC; остальные — ×4/×1.
            if recipe["title"] == "HEXA 4320 Hexacopter":
                assert qty == (6 if category == "motor" else 2 if category == "esc" else 1)
            else:
                assert qty == (4 if category == "motor" else 1)


def test_compat_model_covers_all_part_categories():
    """COMPAT_MODEL покрывает все 20 категорий без пересечений; рамы в
    "recipe" нет (рама — модель "size", выбирается по размеру борта)."""
    grouped = set().union(*COMPAT_MODEL.values())
    assert grouped == set(PART_CATEGORIES)
    assert len(grouped) == sum(len(group) for group in COMPAT_MODEL.values())
    assert "frame" not in COMPAT_MODEL["recipe"]
    assert model_of("camera") == "universal"




