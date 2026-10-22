"""Частоты управления (RX ↔ TX, Этап 5): интервалы в МГц из названий 1С.

Известные «контрольные» частоты: 170 / 300 / 400 / 433 / 450 / 868 / 915 МГц
и 2,4G (2400 МГц). Диапазон «300/1950МГц» — интервал (300, 1950).
Видеочастоты (1080/1360 МГц и ГГц-диапазоны) сюда не попадают: их ловит
frequencies.parse_video_band, а набор известных МГц здесь ограничен.
"""

import re

_KNOWN_MHZ = (170, 300, 400, 433, 450, 868, 915)

# «(300/1950МГц)» — интервал через слэш с суффиксом.
_RANGE_RE = re.compile(r"(\d{3,4})\s*/\s*(\d{3,4})\s*(?:mhz|мгц)")
# «2,4G» / «2.4G» → 2400 МГц.
_2G4_RE = re.compile(r"2[.,]4\s*[ггg]")


def parse_control_freq(name: str) -> tuple[float, float] | None:
    """Частота(ы) управления из названия: (lo, hi) в МГц или None.

    «Передатчик управления (TX) 170 MHz» → (170, 170);
    «Приемник управления ELRS 2-х канальный (300/1950МГц)» → (300, 1950);
    «Приемник HappyModel 2,4G EP1 TCXO» → (2400, 2400);
    «Четвертьволновая антенна 915» → (915, 915) — антенна управления тоже ловится.
    """
    s = str(name).lower()
    m = _RANGE_RE.search(s)
    if m:
        lo, hi = float(m[1]), float(m[2])
        return (min(lo, hi), max(lo, hi))
    if _2G4_RE.search(s):
        return (2400.0, 2400.0)
    # Сначала числа с явным суффиксом MHz/МГц («400MHz», «450МГц»).
    vals = [
        float(v)
        for v in _KNOWN_MHZ
        if re.search(rf"(?<!\d){v}\s*(?:mhz|мгц)", s)
    ]
    if not vals:
        # Без суффикса: ELRS-контекст («ELRS 433») и антенны («антенна 915»).
        vals = [
            float(v)
            for v in _KNOWN_MHZ
            if re.search(rf"(?<!\d){v}(?!\d)", s)
        ]
    if vals:
        return (min(vals), max(vals))
    return None


def freqs_intersect(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Пересечение интервалов частот управления (МГц)."""
    return a[0] <= b[1] and b[0] <= a[1]
