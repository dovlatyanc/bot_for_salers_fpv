"""Рецепты и модель совместимости — защита от несобираемых конфигураций (ЭТАП 10).

Четыре слоя защиты (порядок проверки в review — handlers/builder.py):
  L1 WHITELIST            allowed_union + whitelist_errors: ТОЛЬКО категории
                          модели "recipe" (motor, prop, fc_esc_stack, fc, esc) —
                          деталь обязана входить в объединение деталей активных
                          серийных конфигураций своего размера;
  L2 РЕЦЕПТУРНЫЙ ТИП FC+ESC — fc_esc_shape_errors: 10″ — только стеки,
                          13″ — только раздельные fc+esc, 15″ — оба варианта;
  L3 ПАРАМЕТРИЧЕСКИЕ ПРАВИЛА — services/builder/compat.py (Этап 5; здесь
                          изменён только приоритет статора мотора);
  L4 НАЛИЧИЕ              recipe_builds (карточка рецепта и /recipes: расход
                          по слотам рецепта) и drone_consumption + встроенная
                          проверка остатков compat в review (расход на дрон).

Категории моделей "universal"/"freq"/"size" L1/L2 не образуют: камеры
(видео, тепловизоры, дневные) подходят ЛЮБОМУ дрону, рама выбирается по
размеру (её в рецептах нет вовсе), видеотракт/тракт управления
ограничиваются частотами (L3), а не рецептом.

Зависимости в одну сторону: data/builder ← services/builder ← db ← handlers.
Только чистые функции над dict-структурами (как compat.py) — БД и aiogram
сюда не импортируются.
"""

from __future__ import annotations

import json

from data.builder.recipes import model_of

# Расход моторов на ОДИН дрон (слой L4): серийные квадро-конфигурации ставят
# 4 мотора; гекса-рецепт владельца — 6 (qty слота motor рецепта, Этап 11в).
MOTOR_PER_DRONE = 4

# Обязательные слоты сборки = BUILD_SLOTS (все обязательные). Хотфикс Этапа
# 10а делал обязательность antenna_air условной (VTX-комплект несёт свою
# антенну); хотфикс Этапа 10б вывел антенны/VRX/payload из слотов борта —
# список статичен.
_BASE_REQUIRED = ("frame", "motor", "prop", "fc_esc_stack", "vtx", "camera", "rx")


def _as_slots(slots, title: str = "?") -> list[dict]:
    """Защитный нормалайзер slots рецепта (хотфикс Этапа 10а, Д4).

    Боевая форма краша AttributeError: 'str' object has no attribute 'get' —
    slots приходит ИТЕРАЕМОЙ СТРОКОЙ (JSON-текст не десериализован фасадом
    или сид сохранил список строк): итерация по строке даёт символы. Здесь:
    верхнеуровневая str → json.loads; элемент-не-dict (например строка из
    «списка строк») → ValueError с человекочитаемым текстом — НЕ молчаливый
    AttributeError, а diagnose-сообщение с названием рецепта.
    """
    if isinstance(slots, str):
        try:
            slots = json.loads(slots)
        except ValueError:
            raise ValueError(
                f"recipes: битая форма slots у рецепта {title!r} — текст не JSON"
            ) from None
    if slots is None:
        return []
    if not isinstance(slots, (list, tuple)):
        raise ValueError(
            f"recipes: битая форма slots у рецепта {title!r} — "
            f"ожидается список, получен {type(slots).__name__}"
        )
    for slot in slots:
        if not isinstance(slot, dict):
            raise ValueError(
                f"recipes: битая форма slots у рецепта {title!r} — элемент "
                f"{type(slot).__name__} вместо слота"
            )
    return list(slots)


# ============================================================
# Комплектность: обязательные слоты конкретной сборки (Д8)
# ============================================================
def required_slots(config: dict, parts_by_slot: dict | None = None) -> list[str]:
    """Обязательные слоты сборки: все слоты BUILD_SLOTS (все обязательные).

    Хотфикс Этапа 10а делал обязательность antenna_air условной (VTX-комплект
    несёт свою антенну — рецепты R9/R10); хотфикс Этапа 10б вывел антенны/
    VRX/payload из слотов борта — условность упразднена. Аргументы config/
    parts_by_slot сохранены в контракте вызова (services/builder/compat.py).
    """
    return list(_BASE_REQUIRED)



def recipe_motor_qty(recipe: dict) -> int:
    """Число моторов конфигурации рецепта: qty слота motor (нет слота — 4).

    Работает с обеими формами slots: эталонной ({category: (name, qty)}) и
    БД-формой (список [{category, part_key, name, qty}]) — нормалайзер _as_slots.
    """
    slots = (recipe or {}).get("slots")
    if isinstance(slots, dict):
        spec = slots.get("motor")
        return int(spec[1]) if spec else MOTOR_PER_DRONE
    for slot in _as_slots(slots, str((recipe or {}).get("title") or "?")):
        if slot.get("category") == "motor":
            return int(slot.get("qty") or MOTOR_PER_DRONE)
    return MOTOR_PER_DRONE


# ============================================================
# L1: whitelist — объединение деталей активных рецептов по размеру
# ============================================================
def allowed_union(
    recipes: list[dict], motor_count: int | None = None
) -> dict[int, dict[str, set[str | None]]]:
    """Union деталей рецептов: {size: {category: set(part_key)}}.

    motor_count (Этап 11в): если задан, в union входят только рецепты с таким
    числом моторов (qty слота motor) — whitelist учитывает пару (размер,
    число моторов): квадро-сборка 15″ не предлагает мотор 4320 (он только в
    гекса-рецепте владельца), гекса-сборка 15″ — предлагает именно его.
    None → прежнее поведение (все рецепты размера).

    В union входят ТОЛЬКО категории модели "recipe" (COMPAT_MODEL),
    встречающиеся в активных рецептах: motor, prop, fc_esc_stack, fc, esc.
    Слоты моделей "freq"/"universal"/"size" (камеры, рамы, антенны,
    приёмники…) whitelist не образуют: камеры универсальны, рама выбирается
    по размеру, частоты ограничивает L3.

    part_key=None (неразрешённый слот) в union НЕ попадает: неразрешённая
    деталь не может быть ни выбрана свободно, ни засчитана в whitelist —
    но и не блокирует остальные детали своего рецепта.
    """
    union: dict[int, dict[str, set[str | None]]] = {}
    for recipe in recipes:
        if not recipe.get("is_active", True):
            continue
        if motor_count is not None and recipe_motor_qty(recipe) != int(motor_count):
            continue
        size_union = union.setdefault(recipe["size"], {})
        # Защитный нормалайзер (Д4/10а): slots строкой/списком строк →
        # ValueError с названием рецепта, а не AttributeError в глубине.
        for slot in _as_slots(recipe.get("slots"), str(recipe.get("title") or "?")):
            key = slot.get("part_key")
            if key is None:
                continue
            category = slot["category"]
            if model_of(category) != "recipe":
                continue
            size_union.setdefault(category, set()).add(key)
    return union


def whitelist_errors(
    config: dict, size: int, union: dict[int, dict[str, set[str | None]]]
) -> list[str]:
    """Слой L1: детали вне серийных сборок своего размера (человекочитаемо).

    Проверяются ТОЛЬКО слоты модели "recipe": universal-слоты (камеры,
    аккумуляторы…) подходят любому дрону, "size"/"freq" ограничиваются
    параметрикой L3, а не рецептом. config — {category: {"key": ...,
    "qty": ...}, ...} (как в черновике; имени slot["name"] в нём нет —
    человекочитаемое имя берётся из slot["name"], если оно есть, иначе из
    ключа-имени детали slot["key"]); union — результат allowed_union().
    Слоты без детали пропускаются.
    """
    errors: list[str] = []
    size_union = union.get(size, {})
    for category, slot in (config.get("slots") or {}).items():
        if not slot or not slot.get("key"):
            continue
        if model_of(category) != "recipe":
            continue
        if slot["key"] not in size_union.get(category, set()):
            name = slot.get("name") or slot["key"]
            errors.append(
                f"«{name}»: не применяется в серийных сборках {size}″"
            )
    return errors


# ============================================================
# L2: рецептурная целостность — тип FC+ESC по размеру
# ============================================================
def fc_esc_shape_errors(config: dict, size: int) -> list[str]:
    """Слой L2: стек и раздельные FC/ESC не смешиваются.

    10″ собирается ТОЛЬКО стеком (есть fc или esc по отдельности → ошибка),
    13″ — ТОЛЬКО раздельными FC и ESC (есть стек → ошибка), 15″ — без
    ограничений (в производстве есть оба варианта).
    """
    slots = config.get("slots") or {}
    has_stack = bool(slots.get("fc_esc_stack"))
    has_separate = bool(slots.get("fc")) or bool(slots.get("esc"))
    if size == 10 and has_separate:
        return ["10″ собирается только стеком FC+ESC — раздельные контроллер "
                "и регулятор в серийных сборках не применяются"]
    if size == 13 and has_stack:
        return ["13″ собирается только раздельными FC и ESC — стек в серийных "
                "сборках не применяется"]
    return []


# ============================================================
# L4: наличие — сколько дронов собирается из текущих остатков
# ============================================================
def recipe_builds(
    recipe_slots: list[dict], stock_by_key: dict[str, float], title: str = ""
) -> int:
    """Сколько дронов конкретного рецепта можно собрать из остатков.

    recipe_slots — слоты рецепта (список {category, part_key, name, qty},
    как в БД); min(floor(остаток / qty)) по слотам с part_key и qty>0;
    слот с part_key=None (неразрешённое имя) обнуляет сборку — деталь
    отсутствует в каталоге, рецепт физически не собирается. title — только
    для diagnose-сообщения нормалайзера (битая форма slots).

    РАМА НЕ УЧАСТВУЕТ: рам в рецептах нет вовсе (модель "size" — рама
    выбирается по размеру на шаге оформления), поэтому число совпадает
    с математикой матрицы владельца: «114» = остаток стека, не рам.
    """
    builds: int | None = None
    for slot in _as_slots(recipe_slots, title or "?"):
        key = slot.get("part_key")
        qty = slot.get("qty") or 0
        if key is None or qty <= 0:
            return 0
        have = stock_by_key.get(key, 0)
        available = int(have // qty)
        builds = available if builds is None else min(builds, available)
    return builds if builds is not None else 0


# ============================================================
# L4 в review: расход деталей на ОДИН дрон
# ============================================================
def drone_consumption(
    config: dict, parts_by_slot: dict, motor_count: int | None = None
) -> dict[str, int]:
    """Расход на дрон по заполненным слотам конфига: motor ×motor_count, остальные ×1.

    Результат — {category: qty}; review подставляет его в конфиг перед
    встроенной проверкой остатков compat (check_compatibility) — так L4
    считает расход на дрон, а не «по одной детали». Расход «антенна ×0 при
    VTX-комплекте» упразднён вместе со слотом antenna_air (Этап 10б);
    легаси-слоты старых черновиков считаются ×1 как обычные детали.
    Этап 10в: явный qty слота (рецепты: гекса — 6 моторов, два ESC) не
    меньше дефолта — мотор в свободном режиме (qty=1) считается ×4.
    Этап 11в (C3): расход свободного режима берётся из ВЫБРАННОГО числа
    моторов (state, 4 или 6) — motor_count перекрывает дефолт ×4; пропы —
    1 комплект, как во ВСЕХ рецептах владельца (никаких ceil-формул).
    """
    slots = (config or {}).get("slots") or {}
    consumption: dict[str, int] = {}
    motors = MOTOR_PER_DRONE if motor_count is None else int(motor_count)
    for category, slot in slots.items():
        if not slot:
            continue
        # Этап 10в: qty слота (рецепты: гекса — 6 моторов и два ESC) не
        # меньше дефолта: мотор ×4 в свободном режиме (qty=1), остальные ×1.
        consumption[category] = max(
            int(slot.get("qty") or 1),
            motors if category == "motor" else 1,
        )
    return consumption


# ============================================================
# UI-пометка модели совместимости (шапка part_list)
# ============================================================
def universal_note(category: str) -> str | None:
    """Пометка для шапки part_list: "universal" → «подходит к любому дрону»,
    для остальных моделей — None."""
    if model_of(category) == "universal":
        return "подходит к любому дрону"
    return None
