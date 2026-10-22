"""Производственные рецепты сборок (ЭТАП 10) — ground truth совместимости.

RECIPES — 13 утверждённых владельцем конфигураций (5×10″, 3×13″, 4×15″
+ 1 гексакоптер 15″ — Этап 10в: 6 моторов, два ESC):
имена деталей ТОЧНО как в боевой БД 1С. Из них строится whitelist слотов
(слой защиты L1) и расход остатков (слой L4). Параметрические правила
Этапа 5 пропускают комбинации, которых нет в производстве, — рецепты
закрывают эту дыру: нельзя собрать то, чего не собирает производство.

Формат записи:
    {
        "size": int,                 # дюймы борта
        "title": str,                # название конфигурации (как у владельца)
        "slots": {category: (имя_в_БД, qty) | None, ...},
    }

Расход по умолчанию: мотор — 4 шт на дрон, пропеллеры — 1 комплект,
остальные слоты — 1 шт. None означает «слот в этой конфигурации не
используется» (резерв формата; в актуальных рецептах не встречается —
антенны выведены из слотов борта, хотфикс Этапа 10б).

Если имя из 1С не найдено в parts на момент сида, рецепт всё равно
сидируется (part_key=None) и попадает в отчёт резолвинга — имена НЕ
выдумываются (см. db/repositories_builder.seed_recipes).

МОДЕЛЬ СОВМЕСТИМОСТИ (COMPAT_MODEL) — какая проверка действует на слот:
    "recipe"    motor, prop, fc_esc_stack, fc, esc — whitelist серийных
                сборок (слой L1): только то, что серийно ставим;
    "size"      frame, spare — только параметрика размера (L3); рамы в
                рецептах нет вовсе — она выбирается по размеру борта;
    "freq"      vtx, vrx, antenna_air, antenna_ground, rx, tx — частоты
                (L3), рецептом видеотракт/тракт управления не ограничены
                (антенны/VRX выведены из слотов борта — Этап 10б; группа
                сохранена за категориями каталога);
    "universal" camera, payload, battery, fiber, consumable, finished,
                other — подходят ЛЮБОМУ дрону (камеры: видео,
                тепловизоры, дневные — универсальны).
model_of(category) возвращает группу; неизвестная категория → "universal".
"""

from data.builder.categories import CATEGORY_LABELS  # все 20 категорий каталога

# ============================================================
# модель совместимости: группа проверки для каждой категории слота
# ============================================================
COMPAT_MODEL: dict[str, frozenset[str]] = {
    "recipe": frozenset({"motor", "prop", "fc_esc_stack", "fc", "esc"}),
    "size": frozenset({"frame", "spare"}),
    "freq": frozenset(
        {"vtx", "vrx", "antenna_air", "antenna_ground", "rx", "tx"}
    ),
    "universal": frozenset(
        {"camera", "payload", "battery", "fiber", "consumable", "finished", "other"}
    ),
}


def model_of(category: str) -> str:
    """Группа модели совместимости для категории; неизвестная → "universal"."""
    for model, categories in COMPAT_MODEL.items():
        if category in categories:
            return model
    return "universal"


# Группы покрывают все 20 категорий каталога (guard от рассинхрона с db.models).
assert set().union(*COMPAT_MODEL.values()) == set(CATEGORY_LABELS)

RECIPES: list[dict] = [
    # ------------------------------------------------- 10″ (5 конфигураций)
    {
        "size": 10,
        "title": "ECO F405/60A · BrotherHobby",
        "slots": {
            "motor": ("Мотор для FPV BrotherHobby 3115 900 KV SE", 4),
            "prop": ("Пропеллеры нейлон gemfan 1050 2ccw+2cw", 1),
            "fc_esc_stack": (
                "Полетный контроллер Axisflying ECO F405 "
                "+ регулятор оборотов Axisflying Argus ECO 60A",
                1,
            ),
            "vtx": ("Видеопередатчик Axisflying 3W 5.8ГГц", 1),
            "camera": ("Камера Axisflying Firefly (Разъем 5-28V)", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
    {
        "size": 10,
        "title": "GEPRC Taker 80A · BrotherHobby",
        "slots": {
            "motor": ("Мотор для FPV BrotherHobby 3115 900 KV SE", 4),
            "prop": ("Пропеллеры нейлон gemfan 1050 2ccw+2cw", 1),
            "fc_esc_stack": (
                "Полетный контроллер + регулятор оборотов GEPRC TAKER F722 BLS 80A V2",
                1,
            ),
            "vtx": ("Видеопередатчик Axisflying 3W 5.8ГГц", 1),
            "camera": ("Камера Axisflying Firefly (Разъем 5-28V)", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
    {
        "size": 10,
        "title": "HACKRC 65A · BrotherHobby",
        "slots": {
            "motor": ("Мотор для FPV BrotherHobby 3115 900 KV SE", 4),
            "prop": ("Пропеллеры нейлон gemfan 1050 2ccw+2cw", 1),
            "fc_esc_stack": ("Полетный контроллер + регулятор оборотов HACKRC F7 65А", 1),
            "vtx": ("Видеопередатчик Axisflying 3W 5.8ГГц", 1),
            "camera": ("Камера Axisflying Firefly (Разъем 5-28V)", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
    {
        "size": 10,
        "title": "GEPRC Taker 65A Stack · BrotherHobby",
        "slots": {
            "motor": ("Мотор для FPV BrotherHobby 3115 900 KV SE", 4),
            "prop": ("Пропеллеры нейлон gemfan 1050 2ccw+2cw", 1),
            "fc_esc_stack": ("Полетный контроллер TAKER F722 BT 8Bit 65A Stack", 1),
            "vtx": ("Видеопередатчик Axisflying 3W 5.8ГГц", 1),
            "camera": ("Камера Axisflying Firefly (Разъем 5-28V)", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
    {
        "size": 10,
        "title": "GEPRC Taker 65A · Рагнарек",
        "slots": {
            "motor": ("Моторы 3115 рагнарек для 9 дюймов", 4),
            "prop": ("Пропеллеры нейлон gemfan 1050 2ccw+2cw", 1),
            "fc_esc_stack": ("Полетный контроллер TAKER F722 BT 8Bit 65A Stack", 1),
            "vtx": ("Видеопередатчик Axisflying 3W 5.8ГГц", 1),
            "camera": ("Камера FPV 1800 TVL", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
    # ------------------------------------------------- 13″ (3 конфигурации)
    {
        "size": 13,
        "title": "ECO · 4214 · gemfan 13X10 · Firefly",
        "slots": {
            "motor": ("Мотор axisfly 4214 380KV", 4),
            "prop": ("Пропеллеры нейлон gemfan 13X10 2ccw+2cw", 1),
            "fc": ("Полетный контроллер Axisflying ECO F722", 1),
            "esc": ("Регулятор оборотов Axisflying ECO 8S 100A", 1),
            "vtx": ("Видеопередатчик Axisflying 3W 5.8ГГц", 1),
            "camera": ("Камера Axisflying Firefly (Разъем 5-28V)", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
    {
        "size": 13,
        "title": "ECO · 4214 · HQ 13×9×3 · Black Light",
        "slots": {
            "motor": ("Мотор axisfly 4214 380KV", 4),
            "prop": ("Пропеллер HQ 13?9?3 (2CW+2CCW)", 1),
            "fc": ("Полетный контроллер Axisflying ECO F722", 1),
            "esc": ("Регулятор оборотов Axisflying ECO 8S 100A", 1),
            "vtx": ("Видеопередатчик Axisflying 3W 5.8ГГц", 1),
            "camera": ("Камера Axisflying Black Light", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
    {
        "size": 13,
        "title": "ECO · 4320 · HQ 13×9×3 · Black Light",
        "slots": {
            "motor": ("Мотор axisfly 4320 380KV", 4),
            "prop": ("Пропеллер HQ 13?9?3 (2CW+2CCW)", 1),
            "fc": ("Полетный контроллер Axisflying ECO F722", 1),
            "esc": ("Регулятор оборотов Axisflying ECO 8S 100A", 1),
            "vtx": ("Видеопередатчик Axisflying 3W 5.8ГГц", 1),
            "camera": ("Камера Axisflying Black Light", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },


    # ------------------------------------------------- 15″ (4 конфигурации)
    {
        "size": 15,
        "title": "ECO stack 100A · HQ 15×7×3 · TVL",
        "slots": {
            "motor": ("Мотор axisfly 5315 380KV", 4),
            "prop": ("Пропеллеры HQ 15?7?3 (2CW+2CCW)", 1),
            "fc_esc_stack": (
                "Полетный контроллер Axisflying ECO F722 "
                "+ регулятор оборотов Axisflying Argus ECO 100A",
                1,
            ),
            "vtx": ("Видеопередатчик 4.9G/5.8G/6G 3 Вт + антенна", 1),
            "camera": ("Камера FPV 1800 TVL", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
    {
        "size": 15,
        "title": "ECO fc+esc · HQ 15×7×3 · Black Light",
        "slots": {
            "motor": ("Мотор axisfly 5315 380KV", 4),
            "prop": ("Пропеллеры HQ 15?7?3 (2CW+2CCW)", 1),
            "fc": ("Полетный контроллер Axisflying ECO F722", 1),
            "esc": ("Регулятор оборотов Axisflying ECO 8S 100A", 1),
            "vtx": ("Видеопередатчик 4.9G/5.8G/6G 3 Вт + антенна", 1),
            "camera": ("Камера Axisflying Black Light", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
    {
        "size": 15,
        "title": "ECO fc+esc · gemfan 1510 · Black Light · VTX-комплект",
        "slots": {
            "motor": ("Мотор axisfly 5315 380KV", 4),
            "prop": ("Пропеллеры нейлон gemfan 1510 2ccw+2cw", 1),
            "fc": ("Полетный контроллер Axisflying ECO F722", 1),
            "esc": ("Регулятор оборотов Axisflying ECO 8S 100A", 1),
            "vtx": ("Видеопередатчик Axisflying 3W 5.8ГГц комплект", 1),
            "camera": ("Камера Axisflying Black Light", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
    {
        "size": 15,
        "title": "ECO fc+esc · gemfan 1510 · TVL · VTX-комплект",
        "slots": {
            "motor": ("Мотор axisfly 5315 380KV", 4),
            "prop": ("Пропеллеры нейлон gemfan 1510 2ccw+2cw", 1),
            "fc": ("Полетный контроллер Axisflying ECO F722", 1),
            "esc": ("Регулятор оборотов Axisflying ECO 8S 100A", 1),
            "vtx": ("Видеопередатчик Axisflying 3W 5.8ГГц комплект", 1),
            "camera": ("Камера FPV 1800 TVL", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
    {
        "size": 15,
        "title": "HEXA 4320 Hexacopter",
        "slots": {
            "motor": ("Мотор axisfly 4320 380KV", 6),
            "prop": ("Пропеллеры HQ 15?7?3 (2CW+2CCW)", 1),
            "fc": ("Полетный контроллер Axisflying ECO F722", 1),
            "esc": ("Регулятор оборотов Axisflying ECO 8S 100A", 2),
            "vtx": ("Видеопередатчик 4.9G/5.8G/6G 3 Вт + антенна", 1),
            "camera": ("Камера Axisflying Black Light", 1),
            "rx": ("Приемник (RX) 915MHz", 1),
        },
    },
]
