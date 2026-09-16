"""Каталог АКБ (data/models.py): полнота записей, электрика, разъёмы, подбор."""

from data import (
    CAP_CLASSES, FILTER_LABELS, MODELS, PLATFORMS, SELECTION_DRONE_BY_VOLTAGE,
    SELECTION_STEP_C, VOLTAGES,
)

REQUIRED_FIELDS = {
    "mah", "kg", "cont", "peak", "charge", "mohm", "w",
    "best", "drones", "voltage", "connector", "cap_class",
}


def test_catalog_size_and_fields():
    assert len(MODELS) >= 10
    for code, m in MODELS.items():
        missing = REQUIRED_FIELDS - set(m)
        assert not missing, f"{code}: нет полей {missing}"
        assert m["voltage"] in VOLTAGES, code
        assert m["cap_class"] in CAP_CLASSES, code
        assert set(m["drones"]) <= set(PLATFORMS), code
        assert isinstance(m["best"], str) and m["best"].strip(), code
        assert 0 < m["kg"] < 6 and 0 < m["mah"] == m["mah"], code


def test_electrics_follow_cell_standard():
    """Lishen 2170LA: длительный 35 А × P, пиковый 45 А × P, заряд 0,5C."""
    for code, m in MODELS.items():
        p = m["mah"] // 4000
        assert m["cont"] == 35 * p, code
        assert m["peak"] == 45 * p, code
        assert m["charge"] == m["mah"] * 0.5 / 1000, code
        assert m["peak"] > m["cont"] > 0, code


def test_connectors_by_voltage():
    for code, m in MODELS.items():
        if m["voltage"] == "6S":
            expected = "XT60"
        elif code == "8S2P XT-60":  # нештатный разъём под конкретную задачу
            expected = "XT60"
        else:
            expected = "XT90-S"
        assert m["connector"] == expected, f"{code}: {m['connector']}"


def test_8s_xt60_warns_about_nonstandard_connector():
    """Чек-лист: XT-60 на 8S — нештатный разъём, в карточке должно быть warn."""
    warn = MODELS["8S2P XT-60"].get("warn") or ""
    assert "нештатный" in warn.lower()
    for code, m in MODELS.items():
        if m.get("warn") is not None:
            assert isinstance(m["warn"], str) and m["warn"].strip(), code


def test_selection_data_references_valid_models():
    for voltage, scenarios in SELECTION_STEP_C.items():
        assert voltage in VOLTAGES
        for _, (label, codes) in scenarios.items():
            assert isinstance(label, str) and label.strip()
            assert codes, "пустой список моделей в шаге подбора"
            for code in codes:
                assert code in MODELS, code
    for voltage, drone in SELECTION_DRONE_BY_VOLTAGE.items():
        assert voltage in VOLTAGES
        assert drone == "all" or drone in PLATFORMS


def test_filter_labels_cover_directories():
    assert set(FILTER_LABELS["voltage"]) == set(VOLTAGES) | {"all"}
    assert set(FILTER_LABELS["cap_class"]) == set(CAP_CLASSES) | {"all"}
    assert set(FILTER_LABELS["drone"]) == set(PLATFORMS)