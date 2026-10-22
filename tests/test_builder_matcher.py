"""services/builder/matcher.py «Конструктора» (Этап 6): чистый подбор.

Только чистые функции: parse_fragments / score_part / filter_parts /
apply_slot_filters. БД не трогаем (модуль matcher'а её и не импортирует);
проверки совместимости (compat) покрыты test_compat.py (Этап 5).
"""

from services.builder.matcher import (
    apply_slot_filters,
    filter_parts,
    parse_fragments,
    score_part,
)


def part(key, name, category, params=None, quantity=1.0):
    """PART_DICT в формате фасада list_parts/get_part (params по умолчанию {})."""
    return {
        "id": 0,
        "key": key,
        "name": name,
        "category": category,
        "unit": "шт",
        "params": params or {},
        "quantity": quantity,
        "updated_at": None,
    }


# ---------- parse_fragments ----------

def test_parse_size_stator_kv():
    assert parse_fragments("10 дюймов 3115 900KV") == {"size": 10, "stator": 3115, "kv": 900}


def test_parse_bare_size_fallback():
    # суффикса нет — голый размер 7/9/10/13/15 через detect_size
    assert parse_fragments("Рама 15") == {"size": 15}
    assert parse_fragments("10 3115 900KV") == {"size": 10, "stator": 3115, "kv": 900}
    assert parse_fragments("рама 7") == {"size": 7}


def test_parse_size_suffix_variants():
    assert parse_fragments("Рама 13 дюймов") == {"size": 13}
    assert parse_fragments('10"') == {"size": 10}
    assert parse_fragments("10″") == {"size": 10}
    assert parse_fragments("9 inch") == {"size": 9}


def test_parse_band_power():
    assert parse_fragments("5.8G 4W") == {"band": (5.8, 5.8), "power": 4}
    assert parse_fragments("5,8G")["band"] == (5.8, 5.8)  # запятая → float


def test_parse_freq_current():
    assert parse_fragments("400MHz 60A") == {"freq": 400, "current": 60}
    assert parse_fragments("915мгц") == {"freq": 915}  # кириллица «мгц» → mhz
    assert parse_fragments("60а") == {"current": 60}   # кириллица «а»


def test_parse_current_vs_capacity():
    # «ач» — ёмкость АКБ, не ток: после «а» нет границы слова
    assert parse_fragments("12000мач") == {}
    assert "current" not in parse_fragments("12ah")


def test_parse_empty_and_noise():
    assert parse_fragments("") == {}
    assert parse_fragments("   ") == {}
    assert parse_fragments("просто текст без параметров") == {}


# ---------- score_part ----------

MOTOR = part(
    "m1",
    "Мотор axisfly 3115 900KV",
    "motor",
    {"size": 10, "stator": "3115", "kv": 900},
)


def test_score_all_matched():
    assert score_part(MOTOR, parse_fragments("10 дюймов 3115 900KV")) == 1.0


def test_score_partial():
    # совпали size и stator, kv — нет → 2/3
    assert abs(score_part(MOTOR, {"size": 10, "stator": 3115, "kv": 1400}) - 2 / 3) < 1e-9


def test_score_none_matched():
    assert score_part(MOTOR, {"size": 13, "stator": 4320, "kv": 380}) == 0.0


def test_score_empty_fragments():
    # пустой запрос: нечего сопоставлять → score 1.0 (filter_parts держит все)
    assert score_part(MOTOR, {}) == 1.0
    assert score_part(MOTOR, None) == 1.0


# ---------- filter_parts ----------

def test_filter_min_score_and_sort():
    parts = [
        part("b", "Бета", "motor", {"size": 10, "kv": 900}),
        part("a", "Альфа", "motor", {"size": 10, "kv": 900}),
        part("c", "Гамма", "motor", {"size": 10}),  # score 0.5 < 0.6 — отсекается
    ]
    out = filter_parts(parts, {"size": 10, "kv": 900})
    assert [p["key"] for p in out] == ["a", "b"]  # равный score → name по возрастанию


def test_filter_score_first_then_name():
    parts = [
        part("low", "AAA", "motor", {"size": 10}),              # 0.5
        part("high", "ZZZ", "motor", {"size": 10, "kv": 900}),  # 1.0
    ]
    out = filter_parts(parts, {"size": 10, "kv": 900}, min_score=0.5)
    assert [p["key"] for p in out] == ["high", "low"]


def test_filter_deterministic():
    parts = [part(f"k{i}", f"Деталь {i}", "motor", {"size": 10, "kv": 900}) for i in range(5)]
    assert filter_parts(parts, {"size": 10}) == filter_parts(parts, {"size": 10})


def test_filter_empty_fragments_keeps_all():
    parts = [part("a", "Альфа", "camera", {}), part("b", "Бета", "camera", {})]
    assert filter_parts(parts, {}) == parts


# ---------- apply_slot_filters ----------

def test_slot_category_and_quantity():
    parts = [
        part("f1", "Рама А", "frame", {"size": 10}, quantity=2.0),
        part("f0", "Рама Б", "frame", {"size": 10}, quantity=0.0),  # нет в наличии
        part("m1", "Мотор", "motor", {"stator": "3115"}, quantity=5.0),
    ]
    out = apply_slot_filters(parts, "frame", {})
    assert [p["key"] for p in out] == ["f1"]


def test_motor_size_filter():
    chosen = {"frame": part("f1", "Рама 10 дюймов", "frame", {"size": 10})}
    parts = [
        part("m3115", "Мотор 3115", "motor", {"stator": "3115"}),
        part("m4214", "Мотор 4214", "motor", {"stator": "4214"}),
        part("m4320", "Мотор 4320", "motor", {"stator": "4320"}),
        part("msize", "Мотор для 10", "motor", {"size": 10}),
        part("moff", "Мотор 5315", "motor", {"stator": "5315"}),
    ]
    out = apply_slot_filters(parts, "motor", chosen)
    # Матрица Этапа 10: 10″ → 3115; 4214/4320 (13″) и 5315 (15″) отфильтрованы
    assert [p["key"] for p in out] == ["m3115", "msize"]

    chosen13 = {"frame": part("f13", "Рама 13 дюймов", "frame", {"size": 13})}
    out13 = apply_slot_filters(parts, "motor", chosen13)
    assert [p["key"] for p in out13] == ["m4214", "m4320"]


def test_frame_size_filter_hides_mismatch_and_sizeless():
    # Хотфикс Д2: при известном размере сборки рамы без распознанного
    # размера скрыты из подбора (раньше «оставляли как есть»)
    chosen = {"prop": part("p1", "Проп 10x5x3", "prop", {"size": 10})}
    parts = [
        part("f10", "Рама 10", "frame", {"size": 10}),
        part("f13", "Рама 13", "frame", {"size": 13}),
        part("fnone", "Рама без размера", "frame", {}),  # размер не распознан
    ]
    out = apply_slot_filters(parts, "frame", chosen)
    assert [p["key"] for p in out] == ["f10"]


def test_prop_size_filter_keeps_sizeless():
    # пропы без размера по-прежнему не скрываются (поведение не менялось)
    chosen = {"prop": part("p1", "Проп 10x5x3", "prop", {"size": 10})}
    parts = [
        part("p10", "Проп 10", "prop", {"size": 10}),
        part("p13", "Проп 13", "prop", {"size": 13}),
        part("pnone", "Проп без размера", "prop", {}),
    ]
    out = apply_slot_filters(parts, "prop", chosen)
    assert [p["key"] for p in out] == ["p10", "pnone"]


def test_no_build_size_no_size_filter():
    # размер сборки не определён — размерные фильтры не применяются
    parts = [part("f1", "Рама А", "frame", {"size": 10}), part("f2", "Рама Б", "frame", {"size": 13})]
    assert len(apply_slot_filters(parts, "frame", {})) == 2


def test_band_filter_from_chosen_vtx():
    chosen = {"vtx": part("v1", "VTX 5.8G", "vtx", {"video_band": [5.8, 5.8]})}
    parts = [
        part("a13", "Антенна 1.3G", "antenna_air", {"video_band": [1.3, 1.3]}),
        part("a58", "Антенна 5.8G", "antenna_air", {"video_band": [5.8, 5.8]}),
        part("anone", "Антенна без диапазона", "antenna_air", {}),
    ]
    out = apply_slot_filters(parts, "antenna_air", chosen)
    assert [p["key"] for p in out] == ["a58", "anone"]


def test_band_filter_only_for_vtx_antenna_air():
    chosen = {"vtx": part("v1", "VTX 5.8G", "vtx", {"video_band": [5.8, 5.8]})}
    cam = part("c1", "Камера", "camera", {})
    assert apply_slot_filters([cam], "camera", chosen) == [cam]


def test_freq_filter_from_chosen_tx():
    chosen = {"tx": part("t1", "TX 400MHz", "tx", {"control_freq": [400, 400]})}
    parts = [
        part("r400", "RX 400", "rx", {"control_freq": [400, 400]}),
        part("rrange", "RX 300/1950", "rx", {"control_freq": [300, 1950]}),
        part("r915", "RX 915", "rx", {"control_freq": [915, 915]}),
        part("rnone", "RX без частоты", "rx", {}),
    ]
    out = apply_slot_filters(parts, "rx", chosen)
    assert [p["key"] for p in out] == ["rrange", "r400", "rnone"]


def test_name_sort_deterministic():
    parts = [part("z", "Зета", "camera", {}), part("a", "Альфа", "camera", {})]
    out = apply_slot_filters(parts, "camera", {})
    assert [p["key"] for p in out] == ["a", "z"]


def test_score_missing_key_is_mismatch():
    # ключа kv в params рамы нет — считаем НЕсовпадением, а не пропуском
    frame = part("f1", "Рама 10 дюймов", "frame", {"size": 10})
    assert score_part(frame, {"size": 10, "kv": 900}) == 0.5


def test_score_band_intersect_and_power():
    vtx = part("v1", "DMKR 7.2G 5W VTX", "vtx", {"video_band": [7.2, 7.2], "power_w": 5})
    assert score_part(vtx, {"band": (7.2, 7.2)}) == 1.0
    assert score_part(vtx, {"band": (7.15, 7.15)}) == 1.0  # пересечение (допуск tol)
    assert score_part(vtx, {"band": (5.8, 5.8)}) == 0.0
    assert score_part(vtx, {"power": 5}) == 1.0            # power → params["power_w"]
    assert score_part(vtx, {"power": 4}) == 0.0


def test_score_freq_intersect():
    rx = part("r1", "RX 300/1950MHz", "rx", {"control_freq": [300, 1950]})
    assert score_part(rx, {"freq": 400}) == 1.0
    assert score_part(rx, {"freq": 915}) == 1.0
    assert score_part(rx, {"freq": 2400}) == 0.0
