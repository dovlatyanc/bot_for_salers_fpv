# -*- coding: utf-8 -*-
"""Смоук-проверка «🛠 Конструктора» (Этапы 1–7) — 5 чек-поинтов:

  1) classifier: enrich() на реальных именах (категория + параметры имени,
     в т.ч. хотфикс Д1 — серия Mark4 → размер 10″);
  2) matcher: parse_fragments / filter_parts / apply_slot_filters (в т.ч.
     хотфикс Д2 — рамы без распознанного размера скрыты из подбора);
  3) compat: check_compatibility на владелец-кейсе хотфикса (Д1+Д2+Д4+Д5)
     и derive_build_size (явный size приоритетнее размера рамы);
  4) keyboards: MAX_LABEL_LEN/PAGE_SIZE, trunc_label, kb_part_list (пагинация,
     no-op «dummy»), kb_review;
  5) фасады: db.repositories реэкспортирует builder-фасады, router в
     handlers.builder зарегистрирован.

Запуск: .venv\\Scripts\\python.exe -X utf8 _verify_builder.py
В pytest подключён через tests/test_verify_all.py: main() возвращает 0
(иначе тест красный). БД скрипт не трогает — все чек-поинты чистые функции.
"""
import asyncio
import os
import sys

try:  # кириллица в консоли Windows (в pytest capture — no-op)
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass
# Изоляция от реальной БД: db.engine поднимается при импорте фасадов — даём
# in-memory, если URL ещё не задан (в pytest перекрыт conftest'ом)
os.environ.setdefault("DB_URL", "sqlite+aiosqlite://")
# config.py требует BOT_TOKEN даже для импорта — как tests/conftest.py
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN")
os.environ.setdefault("ADMIN_ID", "1")

import db  # noqa: E402  # первым — рвёт цикл db <-> data.builder.seed_compat
from data.builder.keywords import REAL_EXAMPLES  # noqa: E402
from data.builder.seed_compat import COMPAT_RULES  # noqa: E402
from keyboards import builder as kb  # noqa: E402
from services.builder.classifier import enrich  # noqa: E402
from services.builder.compat import check_compatibility, derive_build_size  # noqa: E402
from services.builder.matcher import (  # noqa: E402
    apply_slot_filters,
    filter_parts,
    parse_fragments,
    score_part,
)


def _part(category, name, params, part_id=1, qty=2.0):
    """PART_DICT-заготовка для matcher/compat (id нужен клавиатурам)."""
    return {
        "id": part_id,
        "key": name.lower(),
        "name": name,
        "category": category,
        "unit": "шт",
        "params": params,
        "quantity": qty,
    }


def check_classifier():
    """Чек-поинт 1: enrich() — категория + параметры имени на реальных именах."""
    frame_cat, frame_params = enrich("Рама карбон Mark4")
    assert frame_cat == "frame", (frame_cat, frame_params)
    assert frame_params.get("size") == 10, frame_params          # хотфикс Д1

    air_cat, air_params = enrich("Четвертьволновая антенна 915")
    assert air_cat == "antenna_air", (air_cat, air_params)
    assert air_params.get("control_freq") == [915.0, 915.0], air_params
    assert "video_band" not in air_params, air_params            # контрольный тракт (Д4/Д5)

    rx_cat, rx_params = enrich("Приемник управления (RX) 400MHz, 100 км")
    assert rx_cat == "rx", (rx_cat, rx_params)
    assert rx_params.get("control_freq") == [400.0, 400.0], rx_params

    # токен mark4/марк4 в боевой выгрузке встречается только у рам
    hits = [
        (category, name)
        for category, names in REAL_EXAMPLES.items()
        for name in names
        if "mark4" in name.lower() or "марк4" in name.lower()
    ]
    assert hits, "в REAL_EXAMPLES нет имён с токеном mark4/марк4"
    assert all(category == "frame" for category, _name in hits), hits
    print("[OK] classifier: enrich() на реальных именах (Mark4 -> 10\", антенна 915 -> control_freq)")


def check_matcher():
    """Чек-поинт 2: parse_fragments / filter_parts / apply_slot_filters."""
    fragments = parse_fragments("10 дюймов 3115 900KV")
    assert fragments.get("size") == 10, fragments
    assert fragments.get("stator") == 3115, fragments
    assert fragments.get("kv") == 900, fragments

    f10 = _part("frame", "Рама 10", {"size": 10}, part_id=10)
    f13 = _part("frame", "Рама 13", {"size": 13}, part_id=13)
    fnone = _part("frame", "Рама без размера", {}, part_id=14)
    p10 = _part("prop", "Проп 10x5x3", {"size": 10}, part_id=20)

    # пустые фрагменты -> score 1.0 у всех
    assert score_part(f10, {}) == 1.0
    assert [p["id"] for p in filter_parts([f10, f13, fnone], {})] == [10, 13, 14]
    # фрагменты по size -> только совпадающие
    assert [p["id"] for p in filter_parts([f10, f13, fnone], {"size": 10})] == [10]

    chosen = {"prop": p10}
    # хотфикс Д2: при известном размере сборки рамы без распознанного
    # размера (и с другим размером) скрыты из подбора
    assert [p["id"] for p in apply_slot_filters([f10, f13, fnone], "frame", chosen)] == [10]
    # пропы без размера по-прежнему не скрываются (совпадение по size — только p10)
    p13 = _part("prop", "Проп 13x4x4", {"size": 13}, part_id=21)
    pnone = _part("prop", "Проп без размера", {}, part_id=22)
    assert [p["id"] for p in apply_slot_filters([p10, p13, pnone], "prop", chosen)] == [20, 22]
    print("[OK] matcher: parse_fragments, filter_parts, apply_slot_filters (безразмерные рамы скрыты)")


def check_compat():
    """Чек-поинт 3: check_compatibility на владелец-кейсе хотфикса."""
    frame = _part("frame", "Рама карбон Mark4", {"size": 10})
    vtx = _part("vtx", "Видеопередатчик Axisflying 3W 5.8ГГц", {"video_band": [5.8, 5.8]})
    air = _part("antenna_air", "Четвертьволновая антенна 915", {"control_freq": [915.0, 915.0]})
    rx = _part("rx", "Приемник управления (RX) 400MHz, 100 км", {"control_freq": [400.0, 400.0]})
    slots = {"frame": frame, "vtx": vtx, "antenna_air": air, "rx": rx}

    bad = check_compatibility({"size": 15, "slots": dict(slots)}, dict(slots), COMPAT_RULES)
    assert bad["ok"] is False, bad
    assert len(bad["errors"]) == 2, bad["errors"]
    error_antenna, error_size = bad["errors"]
    for fragment in (air["name"], rx["name"], "не пересекается с", "915", "400"):
        assert fragment in error_antenna, error_antenna   # хотфикс Д4
    for fragment in (frame["name"], "размер 10″", "не соответствует сборке 15″"):
        assert fragment in error_size, error_size         # хотфикс Д1+Д2
    assert bad["warnings"] == [], bad["warnings"]         # хотфикс Д5
    assert bad["error_details"][0]["suggest_slot"] == "antenna_air", bad["error_details"]

    good = check_compatibility({"size": 10, "slots": dict(slots)}, dict(slots), COMPAT_RULES)
    assert len(good["errors"]) == 1, good["errors"]       # ошибка размера исчезла
    assert good["warnings"] == [], good["warnings"]

    # Д6: derive_build_size — явный size приоритетнее размера рамы
    assert derive_build_size({"slots": dict(slots)}, dict(slots)) == 10
    assert derive_build_size({"size": 15, "slots": dict(slots)}, dict(slots)) == 15

    # Д5 (регресс): антенна без параметров на борту с видео-антенной —
    # прежний warning «не удалось проверить диапазон» сохранён
    noant = {
        "vtx": _part("vtx", "Видеопередатчик Axisflying 3W 5.8ГГц", {"video_band": [5.8, 5.8]}),
        "antenna_air": _part("antenna_air", "Антенна без данных", {}),
    }
    result = check_compatibility(
        {"size": 10, "slots": dict(noant)}, dict(noant), COMPAT_RULES,
    )
    assert result["errors"] == [], result["errors"]
    assert result["warnings"] == [
        "Не удалось проверить диапазон: Антенна без данных",
    ], result["warnings"]
    print("[OK] compat: владелец-кейс (2 ошибки @15\", 1 @10\"), видео-warning сохранён")


def check_keyboards():
    """Чек-поинт 4: константы, trunc_label, kb_part_list, kb_review."""
    assert kb.MAX_LABEL_LEN == 40
    assert kb.PAGE_SIZE == 10
    assert kb.trunc_label("короткое") == "короткое"
    assert kb.trunc_label("А" * 50) == "А" * 39 + "…"

    parts = [
        _part("frame", f"Рама {i}", {"size": 10}, part_id=i) for i in range(1, 13)
    ]
    markup = kb.kb_part_list(parts, page=0, pages=2, slot="frame")
    rows = markup.inline_keyboard
    assert len(rows) == 12, len(rows)                     # 10 деталей + пагинация + [🔍, ⬅]
    assert rows[0][0].callback_data == "b:part:1"
    assert rows[10][1].text == "1/2"
    assert rows[10][1].callback_data == "dummy"           # no-op для счётчика страниц
    assert rows[11][0].callback_data == "b:input"
    assert rows[11][1].callback_data == "b:back"

    review = kb.kb_review().inline_keyboard
    assert [row[0].callback_data for row in review] == ["b:back", "b:save", "b:done"]
    print("[OK] keyboards: MAX_LABEL_LEN/PAGE_SIZE, kb_part_list (1/2 + dummy), kb_review")


def check_facades():
    """Чек-поинт 5: db.repositories реэкспортирует builder-фасады; router."""
    facades = (
        "add_alias", "add_compat_rule", "count_by_category", "delete_alias",
        "delete_draft", "diff_snapshots", "get_available_qty", "get_draft",
        "get_part", "get_part_by_id", "get_recipe", "get_snapshot_rows",
        "list_aliases", "list_compat_rules", "list_drafts", "list_parts",
        "list_recipes", "list_snapshots", "save_draft", "set_compat_rule_active",
    )
    missing = [name for name in facades if not hasattr(db.repositories, name)]
    assert not missing, f"db.repositories не реэкспортирует: {missing}"

    import handlers.builder as builder

    assert builder.router.callback_query.handlers, "нет callback-хендлеров"
    assert builder.router.message.handlers, "нет message-хендлеров"
    print("[OK] фасады: db.repositories (20 builder-фасадов), router handlers.builder")


async def main() -> int:
    try:
        check_classifier()
        check_matcher()
        check_compat()
        check_keyboards()
        check_facades()
    except AssertionError as exc:
        print(f"[FAIL] {exc}")
        return 1
    print("_verify_builder: все 5 чек-поинтов зелёные")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))