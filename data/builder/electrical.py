"""Электрические advisory-нормы «Конструктора» (Этап 5: warning, НЕ error).

Рекомендованные диапазоны KV мотора и минимальный ток ESC/стека по размеру
борта. Значения берутся из названий позиций: extract_kv()/extract_current().
"""

import re

# Рекомендованный диапазон KV мотора по размеру борта (дюймы).
KV_RANGE_BY_SIZE: dict[int, tuple[int, int]] = {
    7: (1000, 1400),
    9: (900, 1100),
    10: (380, 1050),
    13: (300, 450),
    15: (300, 450),
}

# Минимальный ток ESC/стека по размеру борта (А).
MIN_ESC_CURRENT_BY_SIZE: dict[int, int] = {
    7: 40,
    9: 60,
    10: 60,
    13: 100,
    15: 100,
}

# KV только латиницей: кириллическое «кв» даёт ложные срабатывания.
_KV_RE = re.compile(r"(\d{3,4})\s*kv")
# Ток: «60A» / «80А» (латиница или кириллица) после 2–3 цифр;
# для стеков имя содержит ток ESC со своим «80A».
_CURRENT_RE = re.compile(r"(\d{2,3})\s*[aа]\b")


def extract_kv(name: str) -> int | None:
    """KV мотора из имени («Мотор axisfly 3115 900KV» → 900); None если нет."""
    m = _KV_RE.search(str(name).lower())
    return int(m[1]) if m else None


def extract_current(name: str) -> int | None:
    """Ток ESC/стека из имени («…Argus ECO 100A» → 100); None если нет."""
    m = _CURRENT_RE.search(str(name).lower())
    return int(m[1]) if m else None
