"""Подбор кандидатов в слоты «🛠 Конструктора дронов» (Этап 6).

ЧИСТЫЙ модуль (как classifier.py / compat.py): импортирует только stdlib,
справочники data/builder/ и services.builder.compat (тот тоже чистый) —
НИКАКИХ db/, handlers/ или сети. Данные приходят ГОТОВЫМИ от хендлера
(фасады db.repositories_builder); matcher в БД не ходит и ничего не пишет.

Функции:
    parse_fragments(text)          — свободный текст («10 3115 900KV») → dict
                                     фрагментов; в результате только НАЙДЕННЫЕ
                                     ключи: size (дюймы, вкл. голое число
                                     7/9/10/13/15), stator (3115/4214/4320/5315),
                                     kv, band (диапазон → (v, v)), freq (МГц),
                                     current (А), power (Вт);
    score_part(part, fragments)    — доля совпавших фрагментов с params
                                     (0.0..1.0); пустые fragments → 1.0;
    filter_parts(parts, fragments) — score >= min_score (0.6 по умолчанию),
                                     сортировка: score по убыванию, затем name
                                     по возрастанию (детерминировано);
    apply_slot_filters(parts, slot, chosen_parts)
                                   — кандидаты слота относительно уже
                                     выбранных деталей: category == slot,
                                     quantity > 0, размер сборки (motor/frame/
                                     prop) и диапазон/частота (vtx/antenna_air,
                                     tx/rx); сортировка name по возрастанию.

Сравнение чисел — точное (int(float(...)) без эпсилонов, как в compat.py):
в БД количества приходят float, а параметры статора — строкой «3115».
Интервалы params — [lo, hi] (float); фрагмент band — точка (v, v); совпадение
band/freq — пересечение интервалов (bands_intersect / freqs_intersect) или
точное вхождение значения.
"""

from __future__ import annotations

import re

from data.builder.control_freq import freqs_intersect
from data.builder.drone_sizes import MOTOR_STATOR_BY_SIZE, detect_size
from data.builder.frequencies import bands_intersect
from services.builder.compat import derive_build_size

__all__ = [
    "apply_slot_filters",
    "filter_parts",
    "parse_fragments",
    "score_part",
]

# ============================================================
# regex-шаблоны фрагментов (латиница и кириллица, регистр не важен)
# ============================================================
_SIZE_RE = re.compile(r"(\d+)\s*(?:дюйм|″|\"|inch)", re.IGNORECASE)
_STATOR_RE = re.compile(r"\b(31\d\d|42\d\d|43\d\d|53\d\d)\b")
_KV_RE = re.compile(r"(\d+)\s*kv\b", re.IGNORECASE)
_BAND_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*g(?:hz)?\b", re.IGNORECASE)
_FREQ_RE = re.compile(r"(\d+)\s*(?:mhz|мгц)\b", re.IGNORECASE)
# Ток/мощность: латиница и кириллица («60A»/«60А», «4W»/«4Вт») — как
# extract_current / extract_power у classifier'а; «12ач» (ёмкость) не ток:
# после «а» требуется граница слова.
_CURRENT_RE = re.compile(r"(\d+)\s*[aа]\b", re.IGNORECASE)
_POWER_RE = re.compile(r"(\d+)\s*(?:w|вт)\b", re.IGNORECASE)


def parse_fragments(text: str) -> dict:
    """Свободный текст запроса → dict фрагментов (только найденные ключи).

    «10 дюймов 3115 900KV» → {"size": 10, "stator": 3115, "kv": 900};
    «5.8G 4W»              → {"band": (5.8, 5.8), "power": 4};
    «400MHz 60A»           → {"freq": 400, "current": 60};
    «Рама 15»              → {"size": 15} — голый размер 7/9/10/13/15
                             (fallback через detect_size, когда суффикса нет).
    Неизвестные слова игнорируются; без фрагментов → {}.
    """
    text = str(text or "").lower()
    if not text.strip():
        return {}
    fragments: dict = {}
    if m := _SIZE_RE.search(text):
        fragments["size"] = int(m.group(1))
    if m := _STATOR_RE.search(text):
        fragments["stator"] = int(m.group(1))
    if m := _KV_RE.search(text):
        fragments["kv"] = int(m.group(1))
    if m := _BAND_RE.search(text):
        value = float(m.group(1).replace(",", "."))
        fragments["band"] = (value, value)
    if m := _FREQ_RE.search(text):
        fragments["freq"] = int(m.group(1))
    if m := _CURRENT_RE.search(text):
        fragments["current"] = int(m.group(1))
    if m := _POWER_RE.search(text):
        fragments["power"] = int(m.group(1))
    if "size" not in fragments:
        bare = detect_size(text)
        if bare is not None:
            fragments["size"] = bare
    return fragments


# ============================================================
# скоринг / фильтрация
# ============================================================
def _params_get(params: dict, key: str):
    """params по имени фрагмента; power ищется и как power_w (ключ classifier'а),
    band/freq — как video_band / control_freq classifier'а."""
    if key == "power":
        value = params.get("power_w")
        return params.get("power") if value is None else value
    if key == "band":
        return params.get("video_band")
    if key == "freq":
        return params.get("control_freq")
    return params.get(key)


def _matches(params: dict, key: str, fragment) -> bool:
    """Один фрагмент совпал с params? Ключа нет → НЕсовпадение (не пропуск)."""
    value = _params_get(params, key)
    if value is None:
        return False
    if key == "band":
        lo, hi = float(value[0]), float(value[1])
        if bands_intersect((lo, hi), (float(fragment[0]), float(fragment[1]))):
            return True
        # «содержит v» — точное вхождение точки в интервал (без допуска)
        if fragment[0] == fragment[1]:
            point = float(fragment[0])
            return lo <= point <= hi
        return False
    if key == "freq":
        lo, hi = float(value[0]), float(value[1])
        point = float(fragment)
        return freqs_intersect((lo, hi), (point, point)) or lo == hi == point
    # size / stator / kv / current / power — точное числовое совпадение
    try:
        return int(float(value)) == int(float(fragment))
    except (TypeError, ValueError):
        return False


def score_part(part_dict: dict, fragments: dict | None) -> float:
    """Доля совпавших фрагментов (0.0..1.0); пустые fragments → 1.0."""
    fragments = fragments or {}
    if not fragments:
        return 1.0
    params = part_dict.get("params") or {}
    matched = sum(1 for key, fragment in fragments.items() if _matches(params, key, fragment))
    return matched / len(fragments)


def filter_parts(parts: list, fragments: dict | None, min_score: float = 0.6) -> list:
    """Кандидаты по фрагментам: score >= min_score; score убыв., затем name возр."""
    fragments = fragments or {}
    scored = [(score_part(part_dict, fragments), part_dict) for part_dict in parts or ()]
    kept = [(score, part_dict) for score, part_dict in scored if score >= min_score]
    kept.sort(key=lambda item: (-item[0], str(item[1].get("name") or "")))
    return [part_dict for _score, part_dict in kept]


# ============================================================
# фильтрация слота относительно уже выбранных деталей
# ============================================================
_BAND_SLOTS = ("vtx", "antenna_air", "antenna_ground")  # источники video_band
_FREQ_SLOTS = ("tx", "rx")                              # источники control_freq


def _first_band(chosen_parts: dict):
    for category in _BAND_SLOTS:
        part_dict = chosen_parts.get(category) or {}
        band = (part_dict.get("params") or {}).get("video_band")
        if band:
            return (float(band[0]), float(band[1]))
    return None


def _first_freq(chosen_parts: dict):
    for category in _FREQ_SLOTS:
        part_dict = chosen_parts.get(category) or {}
        freq = (part_dict.get("params") or {}).get("control_freq")
        if freq:
            return (float(freq[0]), float(freq[1]))
    return None


def _size_ok(slot: str, params: dict, build_size) -> bool:
    """Размерная совместимость слота с размером сборки (None → не фильтруем)."""
    if slot == "motor":
        if build_size is None:
            return True
        stators = MOTOR_STATOR_BY_SIZE.get(int(build_size)) or []
        stator = params.get("stator")
        if stator is not None and str(stator) in stators:
            return True
        size = params.get("size")
        return size is not None and int(size) == int(build_size)
    if slot == "frame":
        if build_size is None:
            return True
        size = params.get("size")
        # Хотфикс Д2: рама без распознанного размера скрыта из подбора,
        # когда размер сборки известен (раньше «оставляли как есть»)
        return size is not None and int(size) == int(build_size)
    if slot == "prop":
        if build_size is None:
            return True
        size = params.get("size")
        return size is None or int(size) == int(build_size)
    return True


def apply_slot_filters(parts: list, slot: str, chosen_parts: dict | None) -> list:
    """Кандидаты слота относительно уже выбранных деталей (сорт. name возр.).

    chosen_parts — {category: PART_DICT} уже выбранных деталей (хендлер берёт
    их из справочника): одновременно служит parts_by_slot для
    derive_build_size({"slots": chosen}, chosen) (Этап 5). Отфильтровываются:
      • чужая category и quantity <= 0;
      • моторы не под размер сборки (stator вне MOTOR_STATOR_BY_SIZE[size]
        и params["size"] != size), рамы другого размера И рамы без
        распознанного размера (хотфикс Д2); пропы без размера оставляем;
      • vtx/antenna_air в другой диапазон (video_band не пересекается с
        band выбранного vtx/antenna_air; без band — оставляем);
      • tx/rx на другой частоте (control_freq не пересекается с частотой
        выбранного tx/rx; без freq — оставляем).
    """
    chosen_parts = chosen_parts or {}
    build_size = derive_build_size({"slots": chosen_parts}, chosen_parts)
    ref_band = _first_band(chosen_parts)
    ref_freq = _first_freq(chosen_parts)
    out = []
    for part_dict in parts or ():
        if part_dict.get("category") != slot:
            continue
        quantity = part_dict.get("quantity")
        if quantity is None or float(quantity) <= 0:
            continue
        params = part_dict.get("params") or {}
        if not _size_ok(slot, params, build_size):
            continue
        if slot in ("vtx", "antenna_air") and ref_band is not None:
            band = params.get("video_band")
            if band and not bands_intersect(
                (float(band[0]), float(band[1])), ref_band
            ):
                continue
        if slot in ("tx", "rx") and ref_freq is not None:
            freq = params.get("control_freq")
            if freq and not freqs_intersect(
                (float(freq[0]), float(freq[1])), ref_freq
            ):
                continue
        out.append(part_dict)
    out.sort(key=lambda p: str(p.get("name") or ""))
    return out

