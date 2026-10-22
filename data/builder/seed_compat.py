"""Сид правил совместимости «Конструктора» → таблица compat_rules (ЭТАП 2).

Идемпотентно по rule_type (как db.seed.seed_from_json): существующее правило
не трогается, отсутствующее создаётся с is_active=True. Вызывается из
db.init_db() после create_all; коммит делает вызывающий (та же транзакция,
что у остального сида) — функция делает add + flush и возвращает число
созданных правил.

Единственный модуль data/builder, зависящий от db.models (это сид в БД,
а не справочник): сам пакет data/builder остаётся чистыми данными.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CompatRule

# Эталон набора правил (проверяется тестами: 5 шт., rule_type уникальны).
COMPAT_RULES: tuple[dict, ...] = (
    {
        "rule_type": "video_band_match",
        "params": {
            "slots": ["vtx", "vrx", "antenna_air", "antenna_ground"],
            "mode": "intersect",
        },
        "description": "Интервалы видеодиапазона VTX/VRX/антенн должны пересекаться",
    },
    {
        "rule_type": "control_freq_match",
        "params": {"slots": ["tx", "rx"], "mode": "intersect"},
        "description": "Частоты передатчика и приёмника управления должны пересекаться",
    },
    {
        "rule_type": "size_consistency",
        "params": {"slots": ["frame", "motor", "prop"], "mode": "equal_size"},
        "description": "Рама, моторы и пропеллеры должны относиться к одному размеру",
    },
    {
        "rule_type": "esc_current_advisory",
        "params": {"source": "MIN_ESC_CURRENT_BY_SIZE", "severity": "warning"},
        "description": "Ток ESC/стека не ниже рекомендованного для размера",
    },
    {
        "rule_type": "kv_advisory",
        "params": {"source": "KV_RANGE_BY_SIZE", "severity": "warning"},
        "description": "KV мотора в рекомендованном диапазоне для размера",
    },
)


async def seed_compat_rules(session: AsyncSession) -> int:
    """Создаёт отсутствующие правила; возвращает число созданных (0 при повторе)."""
    existing = set(
        (await session.execute(select(CompatRule.rule_type))).scalars().all()
    )
    created = 0
    for spec in COMPAT_RULES:
        if spec["rule_type"] in existing:
            continue
        session.add(CompatRule(
            rule_type=spec["rule_type"],
            params=spec["params"],
            description=spec["description"],
            is_active=True,
        ))
        created += 1
    if created:
        await session.flush()
    return created
