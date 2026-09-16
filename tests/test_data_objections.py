"""База возражений (data/objections.py): структура, стоп-флаги, порядок."""

from data.objections import OBJECTIONS, OBJECTION_ORDER

REQUIRED_FIELDS = {"emoji", "title", "client", "short", "dont", "alt"}


def test_order_matches_items():
    assert len(OBJECTION_ORDER) == len(set(OBJECTION_ORDER)), "дубли в OBJECTION_ORDER"
    assert set(OBJECTION_ORDER) == set(OBJECTIONS)


def test_objection_shape():
    for oid, obj in OBJECTIONS.items():
        missing = REQUIRED_FIELDS - set(obj)
        assert not missing, f"{oid}: нет полей {missing}"
        for field in ("emoji", "title", "client", "short"):
            assert isinstance(obj[field], str) and obj[field].strip(), f"{oid}.{field}"
        assert isinstance(obj["dont"], list) and obj["dont"], oid
        assert isinstance(obj.get("stop"), bool), f"{oid}: stop не bool"


def test_stop_objections():
    """Стоп-возражения — честный отказ; на момент рефакторинга их шесть."""
    stop_ids = {oid for oid, obj in OBJECTIONS.items() if obj.get("stop")}
    assert len(stop_ids) == 6, sorted(stop_ids)
    assert stop_ids <= set(OBJECTION_ORDER)
    for oid in stop_ids:
        assert OBJECTIONS[oid]["title"].strip(), oid


def _strings(obj):
    yield obj.get("emoji", "")
    yield obj.get("title", "")
    yield obj.get("client", "")
    yield obj.get("short", "")
    yield obj.get("detail", "") or ""
    yield obj.get("alt", "") or ""
    yield from obj.get("dont", [])


def test_no_raw_html_in_data():
    for oid, obj in OBJECTIONS.items():
        for s in _strings(obj):
            assert "<" not in s and ">" not in s, f"{oid}: {s[:60]!r}"