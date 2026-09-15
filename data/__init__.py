"""Каталог моделей АКБ и справочники для фильтров (вынесено из bot.py)."""
from .models import (
    CAP_CLASSES,
    FILTER_LABELS,
    MODELS,
    PLATFORMS,
    SELECTION_6S_CONFLICT,
    SELECTION_6S_CONFLICT_LABEL,
    SELECTION_DRONE_BY_VOLTAGE,
    SELECTION_STEP_C,
    SEL_STEP_A_TEXT,
    VOLTAGES,
)

__all__ = [
    "MODELS",
    "PLATFORMS",
    "VOLTAGES",
    "CAP_CLASSES",
    "FILTER_LABELS",
    "SELECTION_STEP_C",
    "SELECTION_6S_CONFLICT",
    "SELECTION_6S_CONFLICT_LABEL",
    "SELECTION_DRONE_BY_VOLTAGE",
    "SEL_STEP_A_TEXT",
]
