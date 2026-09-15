"""Каталог моделей АКБ, справочники фильтров и сценарии продаж (вынесено из bot.py)."""
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
from .scenarios import SCENARIO_ORDER, SCENARIOS

__all__ = [
    "MODELS",
    "PLATFORMS",
    "VOLTAGES",
    "CAP_CLASSES",
    "FILTER_LABELS",
    "SCENARIOS",
    "SCENARIO_ORDER",
    "SELECTION_STEP_C",
    "SELECTION_6S_CONFLICT",
    "SELECTION_6S_CONFLICT_LABEL",
    "SELECTION_DRONE_BY_VOLTAGE",
    "SEL_STEP_A_TEXT",
]
