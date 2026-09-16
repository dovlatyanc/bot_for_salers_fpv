"""Сценарии продаж (data/scenarios.py): структура записей и отсутствие HTML в данных."""

from data import MODELS, SCENARIO_ORDER, SCENARIOS


def test_order_matches_items():
    assert 5 <= len(SCENARIOS) <= 7
    assert set(SCENARIO_ORDER) == set(SCENARIOS)
    assert len(SCENARIO_ORDER) == len(set(SCENARIO_ORDER)), "дубли в SCENARIO_ORDER"


def _strings(sc):
    yield sc.get("emoji", "")
    yield sc.get("title", "")
    yield sc.get("client", "")
    yield from sc.get("why", [])
    for q, a in sc.get("qa", []):
        yield q
        yield a
    for name, why in sc.get("extras", []):
        yield name
        yield why


def test_scenario_shape():
    for sid, sc in SCENARIOS.items():
        for field in ("emoji", "title", "client"):
            assert isinstance(sc.get(field), str) and sc[field].strip(), f"{sid}.{field}"
        rec = sc.get("recommend")
        assert isinstance(rec, list) and rec, sid
        assert len(set(rec)) == len(rec), f"{sid}: дубли в recommend"
        for code in rec:
            assert code in MODELS, f"{sid}: {code} нет в MODELS"
        assert isinstance(sc.get("why"), list) and sc["why"], sid
        qa = sc.get("qa")
        assert isinstance(qa, list) and qa, sid
        for q, a in qa:
            assert isinstance(q, str) and q.strip(), sid
            assert isinstance(a, str) and a.strip(), sid
        for pair in sc.get("extras", []):
            assert isinstance(pair, (tuple, list)) and len(pair) == 2, sid


def test_no_raw_html_in_data():
    """Теги добавляет рендер; в исходных данных HTML-символов быть не должно."""
    for sid, sc in SCENARIOS.items():
        for s in _strings(sc):
            assert "<" not in s and ">" not in s, f"{sid}: {s[:60]!r}"