"""Классификатор деталей «🛠 Конструктора»: название позиции 1С → категория + параметры.

Чистый модуль (Этап 3): импортирует ТОЛЬКО справочники data/builder/ и
stdlib — никаких db/ и handlers/ (зависимости направлены строго в одну
сторону: data/builder ← services/builder/classifier ← db/stock_import).

  classify_part(name)            — проход CLASSIFICATION_RULES по порядку,
                                   первое совпадение match_rule побеждает,
                                   фолбэк — "other";
  extract_power(name)            — мощность VTX («5W», «4Вт») в ваттах;
  RELEVANT_PARAMS                — какие ключи params осмысленны для какой
                                   категории: лишние атрибуты в JSON не
                                   пишутся (правила совместимости Этапа 5
                                   читают только релевантные ключи);
  extract_params(name, category) — распознанные атрибуты имени, отфильтрованные
                                   по RELEVANT_PARAMS[category] (None пропускаются);
  enrich(name)                   — (category, params) одной строкой.

Формат значений params (JSON-совместимый): size/kv/current/power_w — int,
stator — str, video_band/control_freq — список [lo, hi] из float (НЕ tuple).
"""

import re

from data.builder.control_freq import parse_control_freq
from data.builder.drone_sizes import detect_size, extract_stator
from data.builder.electrical import extract_current, extract_kv
from data.builder.frequencies import parse_video_band
from data.builder.keywords import CLASSIFICATION_RULES, match_rule

__all__ = [
    "FALLBACK_CATEGORY",
    "RELEVANT_PARAMS",
    "classify_part",
    "enrich",
    "extract_params",
    "extract_power",
]

# Фолбэк-категория: позиция не опознана правилами, ждёт ручной разметки админом.
FALLBACK_CATEGORY = "other"


def classify_part(name: str) -> str:
    """Название позиции → категория: первое совпавшее правило CLASSIFICATION_RULES.

    Порядок правил критичен (fiber раньше vrx/rx, vtx раньше camera и т.д. —
    причины в комментариях data/builder/keywords.py). Ничего не совпало —
    "other".
    """
    for rule in CLASSIFICATION_RULES:
        if match_rule(name, rule):
            return str(rule["category"])
    return FALLBACK_CATEGORY


# Мощность VTX: «5W», «4Вт», «10 W». Слово-граница на конце и требование
# именно w|вт после цифр отсекают ложные срабатывания: «100 км», «40 кг»,
# «30x30», «270 г» из «градусов» — не мощность.
_POWER_RE = re.compile(r"(\d{1,2})\s*(?:w|вт)\b", re.IGNORECASE)


def extract_power(name: str) -> int | None:
    """Мощность видеопередатчика из названия («DMKR 7.2G 5W VTX» → 5); None если нет."""
    m = _POWER_RE.search(str(name))
    return int(m[1]) if m else None


# Ключи params, осмысленные для категории (для остальных категорий — пустой
# кортеж): params остаётся минимальным, «мусорные» атрибуты не пишутся.
RELEVANT_PARAMS: dict[str, tuple[str, ...]] = {
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

# Контекст управления в имени антенны: широкий диапазон «600/6000» рядом с
# такими токенами — это частоты управления, а не видеодиапазон.
_ANTENNA_CONTROL_CONTEXT = ("elrs", "rx", "tx", "управлени")

# «Голый» числовой диапазон без суффикса («600/6000») в имени антенны —
# МГц видеодиапазона (0.6–6.0 ГГц). С суффиксом МГц диапазоны ловит
# parse_control_freq, а видео-«G»-токены — parse_video_band.
_WIDE_MHZ_RE = re.compile(r"(\d{3,4})\s*/\s*(\d{3,4})")


def _antenna_video_band(name: str) -> tuple[float, float] | None:
    """Спец-эвристика антенн: «600/6000» → (0.6, 6.0) ГГц; None если не нашлось."""
    s = str(name).lower()
    if any(token in s for token in _ANTENNA_CONTROL_CONTEXT):
        return None
    m = _WIDE_MHZ_RE.search(s)
    if not m:
        return None
    lo, hi = float(m[1]) / 1000.0, float(m[2]) / 1000.0
    return (min(lo, hi), max(lo, hi))


def _raw_attrs(name: str) -> dict:
    """Все распознаваемые атрибуты имени (без фильтра по категории)."""
    return {
        "size": detect_size(name),
        "stator": extract_stator(name),
        "kv": extract_kv(name),
        "current": extract_current(name),
        "video_band": parse_video_band(name),
        "control_freq": parse_control_freq(name),
        "power_w": extract_power(name),
    }


def extract_params(name: str, category: str) -> dict:
    """Атрибуты имени, отфильтрованные по RELEVANT_PARAMS[category].

    None-атрибуты пропускаются: в params попадает только реально
    распознанное (не выдумывается). Интервалы video_band/control_freq
    конвертируются в список [lo, hi] из float (JSON-совместимо, НЕ tuple).
    Спец-эвристика антенн: если video_band не распознан, «600/6000» без
    суффикса и без контекста управления считается МГц видеодиапазоном.
    """
    relevant = RELEVANT_PARAMS.get(category, ())
    if not relevant:
        return {}
    attrs = _raw_attrs(name)
    if (
        category in ("antenna_air", "antenna_ground")
        and attrs["video_band"] is None
    ):
        band = _antenna_video_band(name)
        if band is not None:
            attrs["video_band"] = band
    params: dict = {}
    for key in relevant:
        value = attrs.get(key)
        if value is None:
            continue
        if isinstance(value, tuple):
            value = [float(value[0]), float(value[1])]
        params[key] = value
    return params


def enrich(name: str) -> tuple[str, dict]:
    """(category, params) одной строкой: classify_part + extract_params."""
    category = classify_part(name)
    return category, extract_params(name, category)
