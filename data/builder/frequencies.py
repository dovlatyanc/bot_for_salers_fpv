"""Видеодиапазоны FPV-видеолинка: ГГц-интервалы из названий позиций 1С.

Совместимость видео (VTX ↔ VRX ↔ антенны, Этап 5) = пересечение интервалов
bands_intersect(). Диапазоны хранятся как (lo, hi) в ГГц.

Не путать с частотами управления (control_freq.py): имена с маркерами
«elrs» / «rx)» / «(tx» / «управлени» здесь дают None.
"""

import re

# Нормализация числа повторяет _parse_quantity() из db/stock_import.py
# (NBSP/пробелы-разряды, запятая → точка). Импортировать db/ из data/builder
# запрещено (справочники без зависимостей — DoD Этапа 2), поэтому локальная
# копия с ссылкой на источник.
_NBSP = "\u00a0"


def norm_num(s: str) -> float:
    """«4,85» / « 4,85 » / «1 589,000» → float."""
    return float(str(s).replace(_NBSP, " ").replace(" ", "").replace(",", "."))


# Контекст частот управления/ELRS: видеодиапазона в таком имени нет.
_CONTROL_CONTEXT = ("elrs", "rx)", "(tx", "управлени")

# Пара «6,1-7,2 G» — одно суффикс-обозначение диапазона после второго числа.
# Негативный lookahead: после суффикса не буква («270 г» из «градусов» — не ГГц).
_BAND_SUFFIX = r"(?:ггц|гц|г|ghz|g)(?![а-яёa-z])"
_RANGE_G_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)\s*" + _BAND_SUFFIX
)
# Отдельные G-токены: «7.2G», «5,2 ГГц», «4.9G/5.8G/6G».
_G_TOKEN_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*" + _BAND_SUFFIX)
# Диапазон в МГц («1080Mhz to 1360Mhz») — используется только при отсутствии
# явного G-токена (приоритет у «G»).
_MHZ_RANGE_RE = re.compile(r"(\d{3,4})\s*m(?:hz)?\s*(?:to|-|–|—)\s*(\d{3,4})\s*m(?:hz)?")

# «Голое» десятичное число В КОНЦЕ имени («Всенаправленная антенна 5.8») —
# ГГц видеодиапазона, но ТОЛЬКО при маркере видеоконтекста в имени: без него
# «Сервопривод …, 270 градусов» и «Кабель … 1.0мм 7pin» ловились бы зря.
# Мини-фикс 4.0 (долг Этапа 3: «Всенаправленная антенна 5.8» → None).
_VIDEO_CONTEXT = (
    "антенна", "видеопередатчик", "vtx", "vrx",
    "приёмный модуль", "приемный модуль",
)
_BARE_GHZ_RE = re.compile(r"(?<![\d.,])(\d[.,]\d{1,2})$")


def parse_video_band(name: str) -> tuple[float, float] | None:
    """Видеодиапазон из названия: (lo, hi) в ГГц или None.

    «DMKR 7.2G 5W VTX» → (7.2, 7.2);
    «…Rush Solo max 6,1-7,2 G…» → (6.1, 7.2);
    «…4.9G/5.8G/6G…» → (4.9, 6.0) — объединяющий интервал min..max;
    «…1080Mhz to 1360Mhz» → (1.08, 1.36) — только если G-токенов нет;
    «Всенаправленная антенна 5.8» → (5.8, 5.8) — фолбэк «голого» ГГц в конце
    имени, только при маркере видеоконтекста (мини-фикс 4.0).
    """
    s = str(name).lower()
    if any(token in s for token in _CONTROL_CONTEXT):
        return None
    m = _RANGE_G_RE.search(s)
    if m:
        lo, hi = norm_num(m[1]), norm_num(m[2])
        return (min(lo, hi), max(lo, hi))
    vals = [norm_num(v) for v in _G_TOKEN_RE.findall(s)]
    if vals:
        return (min(vals), max(vals))
    m = _MHZ_RANGE_RE.search(s)
    if m:
        lo, hi = float(m[1]) / 1000.0, float(m[2]) / 1000.0
        return (min(lo, hi), max(lo, hi))
    # Фолбэк (мини-фикс 4.0): «голые» ГГц в конце имени у видео-позиций —
    # «Всенаправленная антенна 5.8» → (5.8, 5.8), «Антенна 3.3» → (3.3, 3.3).
    if any(marker in s for marker in _VIDEO_CONTEXT):
        m = _BARE_GHZ_RE.search(s)
        if m:
            x = norm_num(m[1])
            if 1.0 <= x <= 7.9:
                return (x, x)
    return None


def bands_intersect(
    a: tuple[float, float], b: tuple[float, float], tol: float = 0.05
) -> bool:
    """Пересечение ГГц-интервалов (допуск tol — на округления «5.8» vs «5,8»)."""
    return a[0] <= b[1] + tol and b[0] <= a[1] + tol
