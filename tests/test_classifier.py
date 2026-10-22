"""services.builder.classifier: классификация имён позиций 1С и параметры.

Чистые юнит-тесты без БД: эталон — 45 боевых имён REAL_EXAMPLES (Этап 2)
и кейсы-ловушки порядка правил из ТЗ Этапа 3.
"""

import json

from data.builder.keywords import REAL_EXAMPLES

from services.builder.classifier import (
    RELEVANT_PARAMS,
    classify_part,
    enrich,
    extract_params,
    extract_power,
)


def test_real_examples_classify_to_own_category():
    """Все боевые имена REAL_EXAMPLES классификатор относит в свою категорию."""
    total = 0
    for category, names in REAL_EXAMPLES.items():
        for name in names:
            total += 1
            assert classify_part(name) == category, name
    assert total == 47   # фактический размер эталонного словаря (в ТЗ фигурировало 45)


def test_relevant_params_mapping():
    """Таблица релевантных ключей params ровно как в ТЗ; остальных категорий нет."""
    assert RELEVANT_PARAMS == {
        "frame": ("size",),
        "spare": ("size",),
        "motor": ("size", "stator", "kv"),
        "prop": ("size",),
        "fc_esc_stack": ("current",),
        "esc": ("current",),
        "vtx": ("video_band", "power_w"),
        "vrx": ("video_band",),
        "antenna_air": ("video_band", "control_freq"),
        "antenna_ground": ("video_band", "control_freq"),
        "rx": ("control_freq",),
        "tx": ("control_freq",),
    }


def test_rule_order_traps():
    """Ловушки порядка: узкие подстроки побеждают широкие правила ниже."""
    assert classify_part("Модуль приемопередатчика оптического TTL Воздух") == "fiber"
    assert classify_part("Аккумулятор для НСУ для FPV оптоволокна") == "battery"
    assert classify_part(
        "Видеопередатчик с камерой Walksnail Avatar GT KIT (Dual antenna)"
    ) == "vtx"
    assert classify_part("RushFPV 3.3G XSPAN VRX приёмник + патч-антенна") == "vrx"
    assert classify_part("Маунт под антенну для 10 дюймов") == "spare"
    assert classify_part("Рама верхняя часть") == "spare"
    assert classify_part("Пропейлеры для 10 дюймов") == "prop"
    assert classify_part("Удлинитель сервопривода  JR кабель 7,5см") == "spare"


def test_unknown_name_falls_back_to_other():
    """Несуществующая ерунда — фолбэк other."""
    assert classify_part("абракадабра 123") == "other"


def test_extract_power_examples():
    """Мощность VTX: латиница W и кириллица Вт."""
    assert extract_power("DMKR 7.2G 5W VTX") == 5
    assert extract_power("Видеопередатчик Rush solo max 3.3G vtx 4Вт") == 4
    assert extract_power("Видеопередатчик BEASTFPV 3.3GHz 32CH 10W") == 10


def test_extract_power_no_false_positives():
    """«100 км», «40 кг», «270 градусов», «30x30» — не мощность."""
    assert extract_power("Приемник управления (RX) 170 MHz, 100 км") is None
    assert extract_power("Сервопривод DS3240, 40 кг*см, 270 градусов") is None
    assert extract_power(
        "Полётный контроллер RUSHFPV Blade F722 Analog V2 30x30"
    ) is None
    assert extract_power("Рама карбон Mark4") is None


def test_params_motor_full():
    """«Мотор axisfly 3115 900KV»: size в имени не выражен — не выдумывается."""
    assert extract_params("Мотор axisfly 3115 900KV", "motor") == {
        "stator": "3115", "kv": 900,
    }


def test_params_motor_size_only():
    """«Моторы 3115 рагнарек для 9 дюймов»: size из токена, kv отсутствует."""
    assert extract_params("Моторы 3115 рагнарек для 9 дюймов", "motor") == {
        "size": 9, "stator": "3115",
    }


def test_params_vtx_band_and_power():
    assert extract_params(
        "Видеопередатчик Rush Solo max 6,1-7,2 G vtx 3Вт", "vtx"
    ) == {"video_band": [6.1, 7.2], "power_w": 3}


def test_params_rx_control_freq():
    assert extract_params(
        "Приемник axisfly (RX) 400MHz, Модель Generic C3 LR1121 400RX", "rx"
    ) == {"control_freq": [400.0, 400.0]}


def test_params_antenna_ground_wide_mhz_range():
    """Спец-эвристика: «600/6000» без суффикса — МГц видеодиапазон (0.6–6.0 ГГц)."""
    assert extract_params("Антенна направленная 600/6000", "antenna_ground") == {
        "video_band": [0.6, 6.0],
    }


def test_params_antenna_wide_range_ignored_in_control_context():
    """rx/tx/elrs/управлени в имени антенны — широкий диапазон не видеодиапазон."""
    assert extract_params("Антенна 600/6000 RX", "antenna_ground") == {}


def test_params_antenna_air_control_freq():
    assert extract_params("Четвертьволновая антенна 915", "antenna_air") == {
        "control_freq": [915.0, 915.0],
    }


def test_params_frame_size():
    assert extract_params("Рама 10 МАРК4-V2", "frame") == {"size": 10}


def test_params_fc_esc_stack_current():
    assert extract_params(
        "Полетный контроллер + регулятор оборотов GEPRC TAKER F722 BLS 80A V2",
        "fc_esc_stack",
    ) == {"current": 80}


def test_params_irrelevant_category_is_empty():
    """Для категории без релевантных ключей params всегда пустой."""
    assert extract_params("Пластик для 3D принтера PET-G", "consumable") == {}
    assert extract_params("Рама 10 МАРК4-V2", "unknown_category") == {}


def test_band_values_are_json_compatible_lists():
    """video_band/control_freq — списки float (json.dumps без ошибок, не tuple)."""
    params = extract_params(
        "Видеопередатчик Rush Solo max 6,1-7,2 G vtx 3Вт", "vtx"
    )
    assert isinstance(params["video_band"], list)
    assert json.dumps(params)
    rx_params = extract_params(
        "Приемник axisfly (RX) 400MHz, Модель Generic C3 LR1121 400RX", "rx"
    )
    assert isinstance(rx_params["control_freq"], list)
    assert all(isinstance(v, float) for v in rx_params["control_freq"])


def test_enrich_returns_consistent_pair():
    """enrich возвращает (classify_part, extract_params) без расхождений."""
    for name in ("Рама 10 МАРК4-V2", "абракадабра 123", "DMKR 7.2G 5W VTX"):
        category, params = enrich(name)
        assert category == classify_part(name)
        assert params == extract_params(name, category)
    assert enrich("Рама 10 МАРК4-V2") == ("frame", {"size": 10})
