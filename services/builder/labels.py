"""Человекочитаемые подписи «🛠 Конструктора дронов» (Этап 9).

ЧИСТЫЙ модуль: импортирует только data/builder/ (категории). Единственный
источник пользовательских строк конструктора: ни один экран не должен
показывать машинные ключи категорий («frame», «fc_esc_stack»), слово «qty»
или служебные обозначения — только подписи CATEGORY_LABELS и человекочитаемые
числа/параметры.

Функции:
    label(category)            — подпись категории (неизвестная → «Прочее»);
    fmt_qty(q, unit)           — «448 шт», «6,4 упак», «19,5 кг», «1 компл.»;
    fmt_band((lo, hi))         — ГГц-интервал: «5,8 ГГц» / «6,1–7,2 ГГц»;
    fmt_freq((lo, hi))         — МГц-интервал: «400 МГц» / «2,4 ГГц»;
    fmt_params(params)         — параметры через « · » (пусто → «»);
    slot_status_line(cat, part)— строка слота: «⬜ …» / «✅ …».
"""

from __future__ import annotations

from data.builder.categories import CATEGORY_LABELS

__all__ = [
    "fmt_band",
    "fmt_freq",
    "fmt_params",
    "fmt_qty",
    "label",
    "slot_status_line",
]

_FALLBACK_LABEL = "Прочее"  # неизвестная категория — не ключ, а подпись

# Суффиксы единиц: «компл.» — с точкой, остальные — как есть.
_UNIT_SUFFIX = {"компл": "компл."}


def label(category) -> str:
    """Машинный ключ категории → русская подпись (неизвестная → «Прочее»)."""
    return CATEGORY_LABELS.get(str(category or ""), _FALLBACK_LABEL)


def _fmt_number(value) -> str:
    """Число для человека: целое без дроби, дробное — запятая, 1 знак."""
    raw = str(value if value is not None else 0).strip().replace(",", ".")
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value == int(value):
        return str(int(value))
    text = f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return text or "0"


def fmt_qty(q, unit: str | None = "шт") -> str:
    """Количество с единицей: «448 шт», «6,4 упак», «1 компл.» (unit=None → число)."""
    number = _fmt_number(q)
    if unit is None:
        return number
    suffix = _UNIT_SUFFIX.get(str(unit), str(unit)) or "шт"
    return f"{number} {suffix}"


def fmt_band(band) -> str:
    """[lo, hi] в ГГц → «5,8 ГГц» / «6,1–7,2 ГГц»."""
    lo, hi = float(band[0]), float(band[1])
    if lo == hi:
        return f"{_fmt_number(lo)} ГГц"
    return f"{_fmt_number(lo)}–{_fmt_number(hi)} ГГц"


def fmt_freq(band) -> str:
    """[lo, hi] в МГц → «400 МГц» / «300–1950 МГц»; 2400 МГц → «2,4 ГГц»."""
    lo, hi = float(band[0]), float(band[1])
    if lo >= 1000 and hi >= 1000:
        return fmt_band((lo / 1000, hi / 1000))
    if lo == hi:
        return f"{_fmt_number(lo)} МГц"
    return f"{_fmt_number(lo)}–{_fmt_number(hi)} МГц"


def _band_or_str(value) -> str:
    """Интервал (tuple/list) — через fmt_band/fmt_freq, скаляр — как строку."""
    if isinstance(value, (tuple, list)):
        return fmt_band(value)
    return _fmt_number(value)


def fmt_params(params: dict | None) -> str:
    """Параметры детали одной человекочитаемой строкой через « · » (пусто → «»)."""
    params = params or {}
    chunks: list[str] = []
    if params.get("size") is not None:
        chunks.append(f"{_fmt_number(params['size'])}″")
    if params.get("stator") is not None:
        chunks.append(f"статор {params['stator']}")
    if params.get("kv") is not None:
        chunks.append(f"{_fmt_number(params['kv'])} KV")
    if params.get("current") is not None:
        chunks.append(f"{_fmt_number(params['current'])} А")
    if params.get("power_w") is not None:
        chunks.append(f"{_fmt_number(params['power_w'])} Вт")
    if params.get("video_band"):
        chunks.append(_band_or_str(params["video_band"]))
    if params.get("control_freq"):
        freq = params["control_freq"]
        chunks.append(fmt_freq(freq) if isinstance(freq, (tuple, list)) else f"{freq} МГц")
    return " · ".join(chunks)


def slot_status_line(category, part: dict | None) -> str:
    """Строка слота в меню сборки: не выбрано → «⬜ {label} — не выбрано»,
    выбрано → «✅ {label}: {имя} · {кол-во}[ · параметры]»."""
    title = label(category)
    if not part:
        return f"⬜ {title} — не выбрано"
    name = str(part.get("name") or "—")
    line = f"✅ {title}: {name} · {fmt_qty(part.get('quantity'), part.get('unit') or 'шт')}"
    params = fmt_params(part.get("params"))
    if params:
        line += f" · {params}"
    return line
