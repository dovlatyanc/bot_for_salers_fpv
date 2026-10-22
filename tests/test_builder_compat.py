"""Тесты services/builder/compat.py (ЭТАП 5) — чистые, без БД.

Часть деталей собирается через services.builder.classifier.enrich() из
РЕАЛЬНЫХ имён боевой выгрузки 1С (эталоны из data/builder/keywords.py,
REAL_EXAMPLES) — тесты держат связь с боевыми данными: part_dict(name)
даёт key=name.lower(), category/params=enrich(name). Синтетические случаи
(сочетаний нет в выгрузке) помечены комментарием «синтетика» и получают
явный params-override.

ХОТФИКС (пред-Этап-11, зафиксировано в e2e-тестах раздела 6): «Четвертьволновая
антенна 915» — антенна КОНТРОЛЬНОГО тракта (control_freq, video_band не
распознаётся): она участвует в control_freq_match наравне с tx/rx (Д4) и
исключена из warning'а «не удалось проверить диапазон» правила
video_band_match (Д5) — у неё нет видеодиапазона по смыслу, а не из-за
нераспознанных данных. Рамы без распознанного размера дают warning
size_consistency «размер рамы не определён — проверьте вручную» (Д2),
а серия Mark4 распознаётся как 10″ (Д1).
"""

from data.builder.seed_compat import COMPAT_RULES
from services.builder.classifier import enrich
from services.builder.compat import check_compatibility, derive_build_size
from services.builder.recipes import required_slots


# ============================================================
# хелперы
# ============================================================
def rule_of(rule_type: str, *, is_active: bool = True, **param_overrides) -> dict:
    """RULE_DICT на основе ФАКТИЧЕСКИХ params сид-правила (COMPAT_RULES)."""
    for spec in COMPAT_RULES:
        if spec["rule_type"] == rule_type:
            return {
                "id": 0,
                "rule_type": rule_type,
                "params": {**spec["params"], **param_overrides},
                "description": spec["description"],
                "is_active": is_active,
            }
    raise AssertionError(f"нет сида для rule_type={rule_type!r}")


def part_dict(name: str, quantity: float = 1.0, *, params=None) -> dict:
    """PART_DICT из РЕАЛЬНОГО имени: key=name.lower(), category/params=enrich(name).

    params — точечный override для синтетических случаев (по умолчанию —
    распознанные классификатором параметры).
    """
    category, auto_params = enrich(name)
    return {
        "id": 0,
        "key": name.lower(),
        "name": name,
        "category": category,
        "unit": "шт",
        "params": auto_params if params is None else params,
        "quantity": float(quantity),
        "updated_at": None,
    }


def by_slot(*parts) -> dict:
    """PARTS_BY_SLOT: {категория: PART_DICT} — ключ по фактической категории."""
    return {part["category"]: part for part in parts}


def config_for(*parts, size=None, qty=None) -> dict:
    """CONFIG по списку деталей; qty — {категория: qty}, по умолчанию 1."""
    slots = {
        part["category"]: {
            "key": part["key"],
            "qty": (qty or {}).get(part["category"], 1),
        }
        for part in parts
    }
    config = {"slots": slots}
    if size is not None:
        config["size"] = size
    return config


ALL_RULES = [rule_of(spec["rule_type"]) for spec in COMPAT_RULES]


# ============================================================
# 1) video_band_match
# ============================================================
def test_video_band_intersect_ok():
    vtx = part_dict("Видеопередатчик Axisflying 3W 5.8ГГц")            # [5.8, 5.8]
    vrx = part_dict("RushFPV 4.8G - 6.1G XSPAN VRX приёмник")          # [4.8, 6.1]
    ground = part_dict("Патч антенна Maple Wireless 4.85G - 6.3G")     # [4.85, 6.3]
    result = check_compatibility(
        config_for(vtx, vrx, ground), by_slot(vtx, vrx, ground),
        [rule_of("video_band_match")],
    )
    assert result["errors"] == []
    assert result["warnings"] == []


def test_video_band_conflict_error():
    vtx = part_dict("Видеопередатчик Axisflying 3W 5.8ГГц")                    # [5.8, 5.8]
    vrx = part_dict("RushFPV 3.3G XSPAN VRX приёмник + патч-антенна")          # [3.3, 3.3]
    result = check_compatibility(
        config_for(vtx, vrx), by_slot(vtx, vrx), [rule_of("video_band_match")],
    )
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert "не пересекается с" in error
    for fragment in (vtx["name"], vrx["name"], "5.8", "3.3"):
        assert fragment in error


def test_video_band_missing_band_is_warning_not_error():
    vtx = part_dict("Видеопередатчик Axisflying 3W 5.8ГГц")
    # синтетика: VRX без распознаваемого диапазона (в выгрузке таких нет)
    vrx = part_dict("RushFPV XSPAN VRX приёмник", params={})
    result = check_compatibility(
        config_for(vtx, vrx), by_slot(vtx, vrx), [rule_of("video_band_match")],
    )
    assert result["errors"] == []
    assert result["warnings"] == [f"Не удалось проверить диапазон: {vrx['name']}"]


def test_video_band_control_only_antenna_skipped():
    # Хотфикс Д5: антенна контрольного тракта (control_freq, без video_band)
    # не даёт warning «не удалось проверить диапазон» — у неё нет
    # видеодиапазона по смыслу, а не из-за нераспознанных данных
    vtx = part_dict("Видеопередатчик Axisflying 3W 5.8ГГц")
    air = part_dict("Четвертьволновая антенна 915")            # control_freq [915, 915]
    result = check_compatibility(
        config_for(vtx, air), by_slot(vtx, air), [rule_of("video_band_match")],
    )
    assert result["errors"] == []
    assert result["warnings"] == []


def test_video_band_wide_antenna_intersects_everything():
    vtx = part_dict("Видеопередатчик Axisflying 3W 5.8ГГц")       # [5.8, 5.8]
    # «направленная» в классификаторе — antenna_ground (как в боевой БД):
    # правило смотрит обе антенные категории, поэтому категория не важна.
    wide = part_dict("Антенна направленная 600/6000")             # [0.6, 6.0]
    result = check_compatibility(
        config_for(vtx, wide), by_slot(vtx, wide), [rule_of("video_band_match")],
    )
    assert result["errors"] == []
    assert result["warnings"] == []

# ============================================================
# 2) control_freq_match
# ============================================================
def test_control_freq_same_freq_ok():
    rx = part_dict("Приемник axisfly (RX) 400MHz, Модель Generic C3 LR1121 400RX")  # 400
    tx = part_dict("Передатчик axisfly ELRS 400МГц, Модель LR1121 400TX")           # 400
    result = check_compatibility(
        config_for(rx, tx), by_slot(rx, tx), [rule_of("control_freq_match")],
    )
    assert result["errors"] == []
    assert result["warnings"] == []


def test_control_freq_conflict_error():
    tx = part_dict("Передатчик управления (TX) 170 MHz, 100 км")  # 170
    rx = part_dict("Приемник axisfly (RX) 400MHz, Модель Generic C3 LR1121 400RX")  # 400
    result = check_compatibility(
        config_for(rx, tx), by_slot(rx, tx), [rule_of("control_freq_match")],
    )
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    for fragment in (tx["name"], rx["name"], "170", "400"):
        assert fragment in error


def test_control_freq_range_intersects_single():
    rx = part_dict("Приемник управления ELRS 2-х канальный (300/1950МГц)")  # [300, 1950]
    # синтетика: имя в стиле реальных TX, фактической позиции 915 МГц в выгрузке нет
    tx = part_dict("Передатчик управления (TX) 915 MHz, 100 км")            # [915, 915]
    result = check_compatibility(
        config_for(rx, tx), by_slot(rx, tx), [rule_of("control_freq_match")],
    )
    assert result["errors"] == []
    assert result["warnings"] == []


def test_control_freq_2400_displayed_as_2_4g():
    # синтетика по имени: 2,4G TX — в выгрузке только ELRS-приёмники 2,4G
    tx = part_dict("Передатчик управления ELRS 2,4G")              # [2400, 2400]
    rx = part_dict("Приемник управления (RX) 170 MHz, 100 км")     # [170, 170]
    result = check_compatibility(
        config_for(rx, tx), by_slot(rx, tx), [rule_of("control_freq_match")],
    )
    assert len(result["errors"]) == 1
    assert "2.4G" in result["errors"][0]


def test_control_freq_antenna_participates_conflict():
    # Хотфикс Д4: антенна контрольного тракта (control_freq, без video_band)
    # участвует в control_freq_match наравне с tx/rx (якорь — tx):
    # 915 против 400 → ошибка несовпадения частоты
    tx = part_dict("Передатчик управления (TX) 400 MHz, 100 км")   # синтетика: 400
    rx = part_dict("Приемник управления (RX) 400MHz, 100 км")      # 400
    air = part_dict("Четвертьволновая антенна 915")                # control_freq [915, 915]
    result = check_compatibility(
        config_for(tx, rx, air), by_slot(tx, rx, air), [rule_of("control_freq_match")],
    )
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    for fragment in (air["name"], tx["name"], "915", "400"):
        assert fragment in error
    assert result["error_details"][0]["suggest_slot"] == "antenna_air"


def test_control_freq_antenna_same_freq_ok():
    # Хотфикс Д4: контрольная антенна на частоте якоря — без ошибок
    tx = part_dict("Передатчик управления (TX) 915 MHz, 100 км")   # синтетика: 915
    air = part_dict("Четвертьволновая антенна 915")                # 915
    result = check_compatibility(
        config_for(tx, air), by_slot(tx, air), [rule_of("control_freq_match")],
    )
    assert result["errors"] == []
    assert result["warnings"] == []


def test_control_freq_video_antenna_not_in_control_tract():
    # бортовая антенна с video_band остаётся в ВИДЕО тракте: в
    # control_freq_match не участвует (control_freq у неё нет)
    tx = part_dict("Передатчик управления (TX) 400 MHz, 100 км")   # синтетика: 400
    air = part_dict("Антенна axisfly 5.8 GHz RHCP")                # video_band [5.8, 5.8]
    result = check_compatibility(
        config_for(tx, air), by_slot(tx, air), [rule_of("control_freq_match")],
    )
    assert result["errors"] == []
    assert result["warnings"] == []


# ============================================================
# 3) size_consistency (+ derive_build_size)
# ============================================================
def test_size_consistency_ok_10_inches():
    frame = part_dict("Рама 10 МАРК4-V2")                     # size 10
    prop = part_dict("Пропеллер HQProp 10Х5Х3 (2CW+2CCW)")    # size 10
    motor = part_dict("Мотор axisfly 3115 900KV")             # статор 3115 → [9, 10]
    result = check_compatibility(
        config_for(frame, prop, motor), by_slot(frame, prop, motor),
        [rule_of("size_consistency")],
    )
    assert result["build_size"] == 10
    assert result["errors"] == []
    assert result["warnings"] == []


def test_size_consistency_prop_mismatch_error():
    frame = part_dict("Рама 10 МАРК4-V2")
    prop = part_dict("Пропеллеры HQ 15?7?3 (2CW+2CCW)")       # size 15
    result = check_compatibility(
        config_for(frame, prop), by_slot(frame, prop), [rule_of("size_consistency")],
    )
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert "15″" in error and "10″" in error


def test_size_consistency_motor_stator_mismatch_13():
    frame = part_dict("Рама 13 дюймов")                       # size 13
    motor = part_dict("Мотор axisfly 3115 900KV")             # статор 3115 → [9, 10]
    result = check_compatibility(
        config_for(frame, motor), by_slot(frame, motor), [rule_of("size_consistency")],
    )
    assert result["build_size"] == 13
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert "3115" in error and "13″" in error and "допустимы" in error


def test_size_consistency_motor_stator_mismatch_10():
    frame = part_dict("Рама 10 МАРК4-V2")                     # size 10
    motor = part_dict("Мотор axisfly 4214 900KV")             # статор 4214 → [13]
    result = check_compatibility(
        config_for(frame, motor), by_slot(frame, motor), [rule_of("size_consistency")],
    )
    assert result["build_size"] == 10
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert "4214" in error and "10″" in error and "допустимы: 13″" in error


def test_size_consistency_motor_without_size_and_stator_warning():
    frame = part_dict("Рама 10 МАРК4-V2")
    motor = part_dict("Мотор без маркировки")                 # params {} — нет ни size, ни статора
    result = check_compatibility(
        config_for(frame, motor), by_slot(frame, motor), [rule_of("size_consistency")],
    )
    assert result["errors"] == []
    assert len(result["warnings"]) == 1
    assert "не удалось проверить размер мотора" in result["warnings"][0].lower()


def test_size_consistency_motor_stator_priority_over_params_size():
    """Приоритет статора (Этап 10, серийный кейс R5): мотор «…для 9 дюймов»
    (params.size=9, статор 3115) на сборке 10″ проходит без ошибок."""
    frame = part_dict("Рама 10 МАРК4-V2")                     # size 10
    motor = part_dict("Моторы 3115 рагнарек для 9 дюймов")    # size 9, статор 3115
    result = check_compatibility(
        config_for(frame, motor), by_slot(frame, motor), [rule_of("size_consistency")],
    )
    assert result["build_size"] == 10
    assert result["errors"] == []
    assert result["warnings"] == []


def test_size_consistency_motor_params_size_only_without_stator():
    """Статора нет — мотор проверяется по params["size"] (fallback)."""
    frame = part_dict("Рама 10 МАРК4-V2")
    # синтетика: мотор с размером, но без статора (в выгрузке статор есть у всех)
    motor = part_dict("Мотор для 13 дюймов", params={"size": 13})
    result = check_compatibility(
        config_for(frame, motor), by_slot(frame, motor), [rule_of("size_consistency")],
    )
    assert result["build_size"] == 10
    assert len(result["errors"]) == 1
    assert "13″" in result["errors"][0] and "10″" in result["errors"][0]


def test_size_consistency_without_build_size_warning():
    camera = part_dict("Камера FPV 1800 TVL")                 # размер ниоткуда не вывести
    result = check_compatibility(
        config_for(camera), by_slot(camera), [rule_of("size_consistency")],
    )
    assert result["build_size"] is None
    assert result["errors"] == []
    assert result["warnings"] == [
        "Размер сборки не определён — проверка размеров пропущена",
    ]


def test_size_consistency_frame_without_size_warning():
    """Хотфикс Д2: рама без распознанного размера при известном размере
    сборки — warning «размер рамы не определён», а не тишина."""
    # синтетика: рама, у которой размер не распознан (реальная «Рама карбон
    # Mark4» после Д1 распознаётся как 10″)
    frame = part_dict("Рама карбон", params={})
    prop = part_dict("Пропеллер HQProp 10Х5Х3 (2CW+2CCW)")         # size 10
    result = check_compatibility(
        config_for(frame, prop), by_slot(frame, prop), [rule_of("size_consistency")],
    )
    assert result["build_size"] == 10
    assert result["errors"] == []
    assert result["warnings"] == [
        "Рама карбон: размер рамы не определён — проверьте вручную",
    ]


def test_size_consistency_frame15_vs_mark4_error():
    """Хотфикс Д1+Д2: Mark4 распознан как 10″ → против сборки 15″ ошибка."""
    frame = part_dict("Рама карбон Mark4")                         # Д1: size 10
    result = check_compatibility(
        config_for(frame, size=15), by_slot(frame), [rule_of("size_consistency")],
    )
    assert result["build_size"] == 15
    assert result["errors"] == [
        "Рама карбон Mark4: размер 10″ не соответствует сборке 15″",
    ]


def test_derive_build_size_priority():
    frame = part_dict("Рама 10 МАРК4-V2")                     # size 10
    prop = part_dict("Пропеллеры HQ 15?7?3 (2CW+2CCW)")       # size 15
    motor = part_dict("Моторы 3115 рагнарек для 9 дюймов")    # size 9
    camera = part_dict("Камера FPV 1800 TVL")
    # явный размер побеждает всё
    assert derive_build_size({"size": 13, "slots": {}}, {}) == 13
    # рама → проп → мотор
    assert derive_build_size(config_for(frame, prop, motor), by_slot(frame, prop, motor)) == 10
    assert derive_build_size(config_for(prop, motor), by_slot(prop, motor)) == 15
    assert derive_build_size(config_for(motor), by_slot(motor)) == 9
    # без источников — None; слот без детали пропускается
    assert derive_build_size(config_for(camera), by_slot(camera)) is None
    assert derive_build_size({}, {}) is None
    assert derive_build_size(config_for(frame), {}) is None

# ============================================================
# 4) advisory (esc_current_advisory, kv_advisory, is_active, неизвестное правило)
# ============================================================
def test_esc_advisory_below_minimum_warning():
    stack = part_dict(  # current 60
        "Полетный контроллер Axisflying ECO F405 + регулятор оборотов Axisflying Argus ECO 60A",
    )
    result = check_compatibility(
        config_for(stack, size=15), by_slot(stack), [rule_of("esc_current_advisory")],
    )
    assert result["errors"] == []
    assert len(result["warnings"]) == 1
    warning = result["warnings"][0]
    assert "≥ 100 А" in warning and "15″" in warning and "(выбрано 60 А)" in warning


def test_esc_advisory_at_minimum_silent():
    stack = part_dict(  # current 60, минимум для 10″ тоже 60 (граница включительно)
        "Полетный контроллер Axisflying ECO F405 + регулятор оборотов Axisflying Argus ECO 60A",
    )
    result = check_compatibility(
        config_for(stack, size=10), by_slot(stack), [rule_of("esc_current_advisory")],
    )
    assert result["errors"] == []
    assert result["warnings"] == []


def test_esc_advisory_from_esc_slot():
    esc = part_dict("Регулятор оборотов Axisflying ECO 60A")   # current 60
    result = check_compatibility(
        config_for(esc, size=15), by_slot(esc), [rule_of("esc_current_advisory")],
    )
    assert len(result["warnings"]) == 1
    assert "≥ 100 А" in result["warnings"][0]


def test_kv_advisory_out_of_range_warning():
    motor = part_dict("Мотор axisfly 3115 900KV")              # kv 900
    result = check_compatibility(
        config_for(motor, size=15), by_slot(motor), [rule_of("kv_advisory")],
    )
    assert result["errors"] == []
    assert len(result["warnings"]) == 1
    warning = result["warnings"][0]
    assert "KV 900" in warning and "300–450" in warning


def test_kv_advisory_in_range_silent():
    motor = part_dict("Мотор axisfly 3115 900KV")              # kv 900 ∈ [380, 1050] для 10″
    result = check_compatibility(
        config_for(motor, size=10), by_slot(motor), [rule_of("kv_advisory")],
    )
    assert result["warnings"] == []
    assert result["errors"] == []


def test_inactive_rules_silent():
    stack = part_dict(
        "Полетный контроллер Axisflying ECO F405 + регулятор оборотов Axisflying Argus ECO 60A",
    )
    motor = part_dict("Мотор axisfly 3115 900KV")
    rules = [
        rule_of("esc_current_advisory", is_active=False),
        rule_of("kv_advisory", is_active=False),
    ]
    result = check_compatibility(
        config_for(stack, motor, size=15), by_slot(stack, motor), rules,
    )
    assert result["errors"] == []
    assert result["warnings"] == []


def test_unknown_rule_type_warning():
    rule = {"id": 0, "rule_type": "future_unknown", "params": {}, "description": "", "is_active": True}
    result = check_compatibility({}, {}, [rule])
    assert result["warnings"] == ["Правило future_unknown неизвестно, пропущено"]
    assert result["errors"] == []


# ============================================================
# 5) встроенные проверки (комплектность, наличие, остатки)
# ============================================================
def test_empty_config_missing_required_slots():
    """Хотфикс Этапа 10б: обязательных слотов 7 (battery/tx — 10а, антенны/
    VRX/payload — 10б выведены) — missing в порядке required_slots."""
    result = check_compatibility({}, {}, [])
    required = required_slots({}, {})
    assert len(required) == 7
    assert result["missing"] == required
    assert result["complete"] is False
    assert result["ok"] is False
    assert result["errors"] == []                 # неполнота — не error
    assert result["build_size"] is None


def test_part_not_found_error():
    config = {"slots": {"vtx": {"key": "net-takoy-detali", "qty": 1}}}
    result = check_compatibility(config, {}, [])
    assert result["errors"] == ["Деталь не найдена: net-takoy-detali"]


def test_insufficient_stock_error():
    motor = part_dict("Мотор axisfly 3115 900KV", quantity=2.0)
    result = check_compatibility(
        config_for(motor, qty={"motor": 4}), by_slot(motor), [],
    )
    assert result["errors"] == [
        f"Недостаточно на складе: {motor['name']} (нужно 4, доступно 2)",
    ]


def test_to_order_part_warns_instead_of_error():
    """Этап 10в: позиция params["to_order"] (гекса-рама «Рама HEX», нет в
    выгрузке 1С) с нулевым остатком не блокирует сборку — warning, а не
    error (L4). Гекса-рама — отдельная рама: БЕЗ params["size"], размер
    сборки задаётся явно (state) — размерная проверка к ней не применяется."""
    frame = part_dict(
        "Рама HEX (Гексакоптер)", quantity=0.0,
        params={"to_order": True, "hexa": True},
    )
    result = check_compatibility(config_for(frame, size=15), by_slot(frame), [])
    assert result["errors"] == []
    assert result["warnings"] == [f"{frame['name']}: под заказ (нет на складе)"]
    # обычная нулевая позиция — по-прежнему error
    plain = part_dict("Рама 15 дюймов", quantity=0.0)
    result2 = check_compatibility(config_for(plain), by_slot(plain), [])
    assert result2["errors"] == [
        f"Недостаточно на складе: {plain['name']} (нужно 1, доступно 0)",
    ]
    assert result2["warnings"] == []


def test_mto_frame_slot_not_checked_and_not_missing():
    """Этап 11в (B3): mto-слот рамы {key: None, made_to_order: True} — рама
    «под заказ»: не «missing», не даёт «деталь не найдена», не участвует в L4;
    остальные обязательные слоты по-прежнему missing."""
    config = {
        "size": 15,
        "slots": {"frame": {"key": None, "made_to_order": True}},
    }
    result = check_compatibility(config, {}, [])
    assert "frame" not in result["missing"]
    assert not any("frame" in e or "Рама" in e for e in result["errors"])
    assert result["errors"] == []
    # прочие обязательные слоты остаются незакрытыми
    assert {"motor", "prop", "fc_esc_stack", "vtx", "camera", "rx"} <= set(result["missing"])


def test_stock_boundary_inclusive_no_error():
    motor = part_dict("Мотор axisfly 3115 900KV", quantity=4.0)
    result = check_compatibility(
        config_for(motor, qty={"motor": 4}), by_slot(motor), [],
    )
    assert result["errors"] == []


def test_not_found_reported_before_insufficient():
    motor = part_dict("Мотор axisfly 3115 900KV", quantity=2.0)
    config = config_for(motor, qty={"motor": 4})
    config["slots"]["vtx"] = {"key": "net-takoy-detali", "qty": 1}
    result = check_compatibility(config, by_slot(motor), [])
    assert len(result["errors"]) == 2
    assert result["errors"][0] == "Деталь не найдена: net-takoy-detali"
    assert result["errors"][1].startswith("Недостаточно на складе:")

# ============================================================
# 6) end-to-end на реальных именах (все 5 сид-правил)
# ============================================================
def test_e2e_build_10_inches_fully_ok():
    frame = part_dict("Рама 10 МАРК4-V2")
    motor = part_dict("Мотор axisfly 3115 900KV", quantity=10.0)   # qty 4
    prop = part_dict("Пропеллер HQProp 10Х5Х3 (2CW+2CCW)")
    stack = part_dict(
        "Полетный контроллер Axisflying ECO F405 + регулятор оборотов Axisflying Argus ECO 60A",
    )                                                              # current 60 = минимум 10″
    vtx = part_dict("Видеопередатчик Axisflying 3W 5.8ГГц")        # [5.8, 5.8]
    camera = part_dict("Камера FPV 1800 TVL")
    air = part_dict("Антенна Pagoda.5,8 ГГц")                      # [5.8, 5.8]
    vrx = part_dict("RushFPV 4.8G - 6.1G XSPAN VRX приёмник")      # [4.8, 6.1]
    ground = part_dict("Патч антенна Maple Wireless 4.85G - 6.3G") # [4.85, 6.3]
    rx = part_dict("Приемник axisfly (RX) 400MHz, Модель Generic C3 LR1121 400RX")
    tx = part_dict("Передатчик axisfly ELRS 400МГц, Модель LR1121 400TX")
    parts = (frame, motor, prop, stack, vtx, camera, air, vrx, ground, rx, tx)
    result = check_compatibility(
        config_for(*parts, qty={"motor": 4}), by_slot(*parts), ALL_RULES,
    )
    assert result["build_size"] == 10
    assert result["missing"] == []
    assert result["complete"] is True
    assert result["errors"] == []
    assert result["warnings"] == []
    assert result["ok"] is True


def test_e2e_build_15_inches_control_antenna_joins_control_tract():
    # ХОТФИКС (Д4/Д5, см. docstring модуля): «Четвертьволновая антенна 915» —
    # антенна КОНТРОЛЬНОГО тракта (control_freq 915, video_band не
    # распознаётся). Раньше она молча проходила control_freq_match и давала
    # РОВНО ОДИН warning «не удалось проверить диапазон» правила
    # video_band_match. Теперь она участвует в control_freq_match (Д4):
    # против якоря tx 170 МГц — ошибка несовпадения частоты, а из warning'а
    # video_band_match она исключена (Д5) → warnings пуст.
    frame = part_dict("Рама 15 дюймов")
    motor = part_dict("Мотор axisfly 5315 380KV", quantity=8.0)    # qty 4; kv 380 ∈ [300, 450]
    prop = part_dict("Пропеллеры HQ 15?7?3 (2CW+2CCW)")
    stack = part_dict(
        "Полетный контроллер и регулятор оборотов Geprc TAKER F722 BLS 100A 8S",
    )                                                              # current 100 = минимум 15″
    vtx = part_dict("Видеопередатчик Rush Solo max 6,1-7,2 G vtx 3Вт")  # [6.1, 7.2]
    camera = part_dict("Камера FPV 1800 TVL")
    air = part_dict("Четвертьволновая антенна 915")                # control_freq [915, 915]
    rx = part_dict("Приемник управления (RX) 170 MHz, 100 км")
    tx = part_dict("Передатчик управления (TX) 170 MHz, 100 км")
    parts = (frame, motor, prop, stack, vtx, camera, air, rx, tx)
    result = check_compatibility(
        config_for(*parts, qty={"motor": 4}), by_slot(*parts), ALL_RULES,
    )
    assert result["build_size"] == 15
    assert result["missing"] == []
    assert result["complete"] is True
    # Д4: контрольная антенна проверена против якоря (tx, 170 МГц)
    assert result["errors"] == [
        f"{air['name']} (915 МГц) не пересекается с {tx['name']} (170 МГц)",
    ]
    # Д5: из warning'а video_band_match контрольная антенна исключена
    assert result["warnings"] == []
    assert result["ok"] is False
    assert result["error_details"][0] == {
        "text": result["errors"][0],
        "suggest_slot": "antenna_air",
    }


def test_e2e_owner_case_mark4_and_control_antenna():
    # Владельческий кейс хотфикса (Д1+Д2+Д4): рама Mark4 (размера в имени нет
    # → Д1 даёт 10″), vtx 5.8, антенна 915 (контрольная), rx 400 — на сборке
    # 15″ → ДВЕ ошибки: антенна против rx (Д4) и размер рамы против сборки
    # (Д2); та же сборка на 10″ — ошибка размера исчезает.
    frame = part_dict("Рама карбон Mark4")                         # Д1: size 10
    vtx = part_dict("Видеопередатчик Axisflying 3W 5.8ГГц")        # [5.8, 5.8]
    air = part_dict("Четвертьволновая антенна 915")                # control_freq [915, 915]
    rx = part_dict("Приемник управления (RX) 400MHz, 100 км")      # [400, 400]
    parts = (frame, vtx, air, rx)
    rules = [
        rule_of("video_band_match"),
        rule_of("control_freq_match"),
        rule_of("size_consistency"),
    ]
    bad = check_compatibility(config_for(*parts, size=15), by_slot(*parts), rules)
    assert bad["ok"] is False
    assert bad["errors"] == [
        f"{air['name']} (915 МГц) не пересекается с {rx['name']} (400 МГц)",
        "Рама карбон Mark4: размер 10″ не соответствует сборке 15″",
    ]
    assert bad["warnings"] == []                    # Д5: видео-warning не возвращается
    assert bad["error_details"][0]["suggest_slot"] == "antenna_air"
    assert bad["error_details"][1]["suggest_slot"] is None
    good = check_compatibility(config_for(*parts, size=10), by_slot(*parts), rules)
    assert good["errors"] == [
        f"{air['name']} (915 МГц) не пересекается с {rx['name']} (400 МГц)",
    ]
    assert good["warnings"] == []


# ============================================================
# 7) детерминированность
# ============================================================
def test_deterministic_same_input_same_result():
    # «грязный» вход: конфликт диапазонов, нехватка, неизвестное и
    # выключенное правило — чтобы сравнение было осмысленным.
    vtx = part_dict("Видеопередатчик Axisflying 3W 5.8ГГц")            # [5.8, 5.8]
    vrx = part_dict("RushFPV 3.3G XSPAN VRX приёмник + патч-антенна")  # [3.3, 3.3]
    motor = part_dict("Мотор axisfly 3115 900KV", quantity=2.0)
    tx = part_dict("Передатчик управления (TX) 170 MHz, 100 км")
    config = config_for(vtx, vrx, motor, tx, qty={"motor": 4})
    parts = by_slot(vtx, vrx, motor, tx)
    rules = [
        *ALL_RULES,
        {"id": 0, "rule_type": "future_unknown", "params": {}, "description": "", "is_active": True},
        rule_of("esc_current_advisory", is_active=False),
    ]
    first = check_compatibility(config, parts, rules)
    second = check_compatibility(config, parts, rules)
    assert first["errors"] and first["warnings"]      # сравнивать есть что
    assert first == second





# ============================================================
# 7) Этап 10в: раздельная пара fc+esc закрывает слот стека в missing
# ============================================================
def test_fc_esc_pair_covers_stack_slot():
    """fc и esc вместе (рецепты 13″/15″) — fc_esc_stack не «missing»."""
    fc = part_dict("Полетный контроллер Axisflying ECO F722")
    esc = part_dict("Регулятор оборотов Axisflying ECO 8S 100A")
    result = check_compatibility(config_for(fc, esc), by_slot(fc, esc), [])
    assert "fc_esc_stack" not in result["missing"]


def test_single_fc_does_not_cover_stack_slot():
    """Одиночный fc (без esc) пару не закрывает — слот стека «missing»."""
    fc = part_dict("Полетный контроллер Axisflying ECO F722")
    result = check_compatibility(config_for(fc), by_slot(fc), [])
    assert "fc_esc_stack" in result["missing"]
