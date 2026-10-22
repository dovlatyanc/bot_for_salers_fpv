"""Подсказки замен для экрана проверки «🛠 Конструктора» (ЭТАП 11, B3).

ЧИСТЫЙ модуль (как compat.py / matcher.py): импортирует только stdlib,
справочники data/builder/ и чистые функции services/builder/ — НИКАКИХ db/,
handlers/, сети. Данные (config, available_parts) приходят ГОТОВЫМИ от
вызывающего (рендер review в handlers/builder.py берёт их из state и
list_parts) — модуль в БД не ходит и ничего не пишет.

    suggest_alternatives(suggest_slot, config, available_parts, limit=2)
        — кандидаты на замену в слоте suggest_slot: та же категория,
          остаток > 0, ключ ≠ выбранного, проходят модель совместимости
          категории (data/builder/recipes.py COMPAT_MODEL):
            "recipe"    — ключ в union сид-рецептов размера сборки (L1);
            "freq"      — диапазон/частота пересекается с якорем тракта
                          (деталь без параметра — остаётся, как в matcher);
            "size"      — params.size совпадает с размером сборки
                          (без размера — остаётся);
            "universal" — любые.
          Сортировка по остатку УБЫВАНИЕ (устойчивая: при равных остатках —
          порядок справочника). Возвращает PART_DICT-кандидаты целиком;
          имена на экране усекает вызывающий (trunc_label(…, 46), компактный
          режим — 32).

Якорь тракта — первый слот с непустым диапазоном в каноническом порядке
сид-правил compat (data/builder/seed_compat.py: video_band_match —
vtx → vrx → antenna_air → antenna_ground; control_freq_match — tx → rx);
порядок захардкожен, чтобы модуль оставался чистым (seed_compat зависит
от db.models и сюда не импортируется).

Ограничение: whitelist для модели "recipe" строится из сид-рецептов
RECIPES (data/builder/recipes.py) — рецепты, добавленные админом поверх
сида, в подсказках не участвуют (L1-проверка в review при этом считает
по БД). Ключи — part_key = clean_name(name).lower(); имена рецептов не
имеют краевой пунктуации, поэтому достаточно нормализации пробелов.
"""

from __future__ import annotations

import re

from data.builder.control_freq import freqs_intersect
from data.builder.frequencies import bands_intersect
from data.builder.recipes import RECIPES, model_of
from services.builder.compat import derive_build_size

__all__ = ["suggest_alternatives"]

# Канонический порядок частотных слотов — как в сид-правилах compat (Этап 5):
# якорь = первый слот с непустым диапазоном/частотой.
_VIDEO_ORDER = ("vtx", "vrx", "antenna_air", "antenna_ground")
_FREQ_ORDER = ("tx", "rx")


def _name_key(name: str) -> str:
    """Имя 1С → ключ parts.key: NBSP → пробел, схлопывание пробелов, lower."""
    s = str(name).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def _seed_union() -> dict[int, dict[str, set[str]]]:
    """Whitelist серийных сборок из RECIPES: {size: {category: {part_key}}}.

    Только категории модели "recipe" (как allowed_union, Этап 10); ключ —
    part_key имени из 1С. None-слоты (антенна при VTX-комплекте) мимо.
    """
    union: dict[int, dict[str, set[str]]] = {}
    for recipe in RECIPES:
        size_union = union.setdefault(int(recipe["size"]), {})
        for category, entry in (recipe.get("slots") or {}).items():
            if entry is None or model_of(category) != "recipe":
                continue
            size_union.setdefault(category, set()).add(_name_key(entry[0]))
    return union


_SEED_UNION = _seed_union()


def _parts_by_key(available_parts) -> dict[str, dict]:
    """{parts.key: PART_DICT} по полному справочнику (один проход)."""
    return {p["key"]: p for p in (available_parts or ()) if p.get("key")}


def _build_size(config: dict, available_parts) -> int | None:
    """Размер сборки: config["size"] → params.size рамы/пропа/мотора (compat)."""
    config = config or {}
    if config.get("size") is not None:
        return int(config["size"])
    slots = config.get("slots") or {}
    parts = _parts_by_key(available_parts)
    parts_by_slot = {
        category: part
        for category, slot in slots.items()
        if (part := parts.get((slot or {}).get("key"))) is not None
    }
    return derive_build_size({"slots": slots}, parts_by_slot)


def _anchor_band(config, available_parts) -> tuple[float, float] | None:
    """Якорь видеотракта: первый слот _VIDEO_ORDER с непустым video_band."""
    parts = _parts_by_key(available_parts)
    slots = (config or {}).get("slots") or {}
    for category in _VIDEO_ORDER:
        part = parts.get((slots.get(category) or {}).get("key"))
        band = ((part or {}).get("params") or {}).get("video_band")
        if band:
            return (float(band[0]), float(band[1]))
    return None


def _anchor_freq(config, available_parts) -> tuple[float, float] | None:
    """Якорь тракта управления: первый слот _FREQ_ORDER с непустой частотой."""
    parts = _parts_by_key(available_parts)
    slots = (config or {}).get("slots") or {}
    for category in _FREQ_ORDER:
        part = parts.get((slots.get(category) or {}).get("key"))
        freq = ((part or {}).get("params") or {}).get("control_freq")
        if freq:
            return (float(freq[0]), float(freq[1]))
    return None


def suggest_alternatives(suggest_slot, config, available_parts, limit=2) -> list[dict]:
    """Кандидаты на замену в слоте suggest_slot (Этап 11, B3).

    suggest_slot — категория слота (suggest_slot из error_details результата
    check_compatibility, Этап 11); config — {"slots": {...}, "size": int | None}
    сборки; available_parts — ПОЛНЫЙ справочник деталей (список PART_DICT из
    list_parts). Возвращает до limit PART_DICT-кандидатов, отсортированных
    по остатку убывание; имена усекает вызывающий (trunc_label(…, 46)).
    """
    category = str(suggest_slot or "")
    if not category:
        return []
    config = config or {}
    slots = config.get("slots") or {}
    current_key = (slots.get(category) or {}).get("key")
    model = model_of(category)
    size: int | None = None
    if model in ("recipe", "size"):
        size = _build_size(config, available_parts)
    band = freq = None
    if model == "freq":
        if category in _FREQ_ORDER:
            freq = _anchor_freq(config, available_parts)
        else:
            band = _anchor_band(config, available_parts)
    candidates: list[dict] = []
    for part in available_parts or ():
        if part.get("category") != category:
            continue
        try:
            quantity = float(part.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0.0
        if quantity <= 0:
            continue
        key = part.get("key")
        if not key or key == current_key:
            continue
        params = part.get("params") or {}
        if model == "recipe":
            # Размер не определён → whitelist не применить (как в matcher).
            if size is not None and key not in _SEED_UNION.get(size, {}).get(
                category, set()
            ):
                continue
        elif model == "freq":
            if category in _FREQ_ORDER:
                value = params.get("control_freq")
                if freq is not None and value and not freqs_intersect(
                    (float(value[0]), float(value[1])), freq
                ):
                    continue
            else:
                value = params.get("video_band")
                if band is not None and value and not bands_intersect(
                    (float(value[0]), float(value[1])), band
                ):
                    continue
        elif model == "size":
            part_size = params.get("size")
            if size is not None and part_size is not None and int(part_size) != size:
                continue
        # "universal" — подходит любому дрону, без ограничений.
        candidates.append(part)
    candidates.sort(key=lambda p: float(p.get("quantity") or 0), reverse=True)
    return candidates[: max(0, int(limit))]
