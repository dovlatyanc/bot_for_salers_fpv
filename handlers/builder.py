"""«🛠 Конструктор дронов»: FSM-сборка конфигурации из боевой БД (Этап 6).

Хендлеры ТОНКИЕ: подбор кандидатов — services/builder/matcher.py (чистый),
проверка совместимости — services/builder/compat.py (Этап 5), раскладка
клавиатур — keyboards/builder.py, данные — фасады db.repositories_builder
(реэкспорт db.repositories). Здесь только orchestration и рендер.

BuilderState (states/states.py) и FSM-данные (state.data):
    slots        {category: {"key": str, "qty": int}} — выбранное
                 (формат CONFIG["slots"] из services/builder/compat.py);
    draft_id     int | None — черновик, в который сохранять (resume/save);
    current_slot str | None — слот, заполняемый на part_list/part_input;
    page         int — текущая страница part_list (0-based);
    filters      dict — фрагменты поиска part_input (matcher.parse_fragments);
    mode         "recipe" | "free" — режим (Этап 10; None в черновиках);
    size         int | None — выбранный размер борта (Этап 10).

Поток (callback-data зафиксированы, см. keyboards/builder.py):
  1) /builder → BuilderState.start: «Конструктор FPV» + [📦 Серийные
     конфигурации, 🛠 Свободная сборка, 📂 Возобновить последний черновик,
     🗂 Все черновики];
  1а) b:mode:recipe | b:mode:free → choosing_size: [10″, 13″, 15″] — только
     размеры, по которым есть рецепты (Этап 10);
  2) серийный режим: b:size:<n> → recipe_list: активные рецепты размера
     «{title} — можно собрать: N шт» (recipe_builds по текущим остаткам);
     b:recipe:<id> → recipe_card: состав по слотам человекочитаемо
     (camera/vtx/rx — «производственный пример», рама — «любая {size}″») +
     [🛠 Оформить сборку, ⬅ Назад]; b:recipe:build:<id> → slots из рецепта →
     меню слотов (mode="recipe", size=<n>; слот рамы пуст — рамы в рецептах
     нет); b:recipe:back → список рецептов;
     свободный режим: b:size:<n> → (если по размеру есть сборки с разным
     числом моторов — экран b:motors:<n>, Этап 11в) → slot_menu
     (mode="free", size=<n>, motor_count=<n>): 7 слотов BUILD_SLOTS +
     [📋 Итог, 💾 Сохранить, ❌ Отмена]; слот frame при 6/8 моторах — экран
     «под заказ» (b:mto_frame → mto-слот key=None, made_to_order=True);
  3) b:slot:<category> → current_slot, page=0, filters={} → part_list:
     list_parts(category=slot) → matcher.apply_slot_filters(…, slot, chosen)
     → matcher.filter_parts(filters) → ФИЛЬТР ПО МОДЕЛИ СОВМЕСТИМОСТИ
     (Этап 10, свободный режим): "recipe" — только allowed_union(size),
     union пуст → «для {size}″ этот узел собирается иначе» + кнопка в
     серийный режим; "freq" — пересечение диапазона/частоты уже отфильтровал
     matcher; "size" — params.size == size (рама/запчасти); "universal" —
     все детали с остатком > 0 + universal_note в шапке;
  4) b:part:<id> (int parts.id, Этап 9а — ключи-имена длинных деталей
      превышали лимит Telegram 64 байта на callback_data) → get_part_by_id
      → slots[slot]={"key": part["key"], "qty": 1} → slot_menu (в state
      и черновиках — ключи-имена);
  5) b:page:+1/-1 → ре-рендер part_list (page зажимается границами);
  6) b:input → part_input: текст → parse_fragments → filters → part_list (0);
  7) b:review → review: ПОРЯДОК ПРОВЕРОК (Этап 10): L1 whitelist рецептов
     (только категории "recipe") → L2 тип FC+ESC → L3 параметрические
     правила (compat, Этап 5) → L4 наличие (расход на дрон: motor ×4,
     остальные ×1; явный qty слота учитывается — гекса Этапа 10в: моторы
     ×6, ESC ×2); все строки человекочитаемые;
  8) b:save → save_draft(user_id, {"slots": slots}, name=«Черновик ДД.ММ.ГГГГ
     ЧЧ:ММ», draft_id из state) → подтверждение callback.answer;
  9) b:done → state.clear() → «Конфигурация сохранена. /builder»;
 10) b:cancel → state.clear() → экран start;
 11) b:start:drafts → drafts: list_drafts(user_id) + [📥, 🗑, ⬅ Назад];
 12) b:draft:load:<id> → get_draft(id, user_id) → slots из config → slot_menu;
 13) b:draft:del:<id> → delete_draft(id, user_id) → ре-рендер drafts;
     b:back — на уровень вверх: drafts → start, recipe_list → выбор размера,
     part_list/review → slot_menu; b:recipe:back — карточка → список рецептов.

Редактирование: edit_text где возможно, при сбое — answer (fallback, как в
handlers/selection.py). Входящие сообщения пользователя (part_input) всегда
answered. Регистрация — явная router.<update>.register(...) без декораторов
(соглашение проекта); router подключается в bot.py после build_router.
"""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from config import system_logger
from data.builder.categories import BUILD_SLOTS
from db.repositories import (
    delete_draft,
    get_draft,
    get_part_by_id,
    get_recipe,
    list_compat_rules,
    list_drafts,
    list_parts,
    list_recipes,
    save_draft,
)
from services.builder.labels import fmt_params, fmt_qty, label, slot_status_line
from keyboards.builder import (
    PAGE_SIZE,
    kb_drafts,
    kb_frame_mto,
    kb_motor_count,
    kb_part_list,
    kb_recipe,
    kb_recipe_list,
    kb_review,
    kb_size_menu,
    kb_slot_menu,
    kb_start,
    trunc_label,
)
from services.builder.compat import check_compatibility, derive_build_size
from services.builder.matcher import apply_slot_filters, filter_parts, parse_fragments
from services.builder.recipes import (
    allowed_union,
    drone_consumption,
    fc_esc_shape_errors,
    model_of,
    recipe_builds,
    recipe_motor_qty,
    universal_note,
    whitelist_errors,
)
from states import BuilderState
from utils.ui import safe_answer

router = Router(name="builder")

# Категории, образующие конфигурацию борта. Черновики могли быть созданы до
# выведения battery/tx (хотфикс Этап 10а) и антенн/VRX/payload (хотфикс
# Этап 10б) из BUILD_SLOTS: легаси-слоты не переписываются молча, а защитно
# отфильтровываются при чтении в state / рендер / review (_slots_in_build) —
# на диске остаются как есть.
# Этап 10в: fc/esc — раздельная форма слота fc_esc_stack (рецепты 13″ и
# гекса 15″); в BUILD_SLOTS их нет, но в конфиге борта они легитимны и
# вместе закрывают слот стека (замена в check_compatibility).
_MENU_SLOT_CATEGORIES = frozenset(cat for cat, _req, _title in BUILD_SLOTS)
_BUILD_SLOT_CATEGORIES = _MENU_SLOT_CATEGORIES | {"fc", "esc"}


def _slots_in_build(slots: dict | None) -> dict:
    """Защитный фильтр легаси-черновиков: battery/tx (10а) и антенны/VRX/payload (10б) — не слоты борта; fc/esc (10в) остаются — раздельная форма слота стека."""
    return {c: s for c, s in (slots or {}).items() if c in _BUILD_SLOT_CATEGORIES}


# ============================================================
# рендер-помощники
# ============================================================
async def _reply(target, text: str, kb, edit: bool = True) -> None:
    """edit_text где возможно; при сбое — answer (edit=False — всегда answer)."""
    if edit:
        try:
            await target.edit_text(text, reply_markup=kb)
            return
        except Exception as e:  # noqa: BLE001 — любое отклонение → fallback
            system_logger.warning(f"Builder edit failed: {e}")
    await target.answer(text, reply_markup=kb)


async def _load_chosen_parts(slots: dict | None) -> dict:
    """{category: PART_DICT} выбранных слотов (matcher + рендер + compat).

    Один проход list_parts() вместо get_part на каждый слот — справочник
    маленький (двойной цикл дешевле N+1-запросов).
    """
    slots = slots or {}
    key_to_cat = {sl["key"]: cat for cat, sl in slots.items() if sl.get("key")}
    if not key_to_cat:
        return {}
    chosen: dict = {}
    for part in await list_parts():
        category = key_to_cat.get(part["key"])
        if category is not None:
            chosen[category] = part
    return chosen


def _fragments_repr(fragments: dict) -> str:
    """Фрагменты поиска одной человекочитаемой строкой («10″ 3115 900KV»)."""
    chunks = []
    if "size" in fragments:
        chunks.append(f"{fragments['size']}″")
    if "stator" in fragments:
        chunks.append(str(fragments["stator"]))
    if "kv" in fragments:
        chunks.append(f"{fragments['kv']}KV")
    if "band" in fragments:
        chunks.append(f"{fragments['band'][0]:g}G")
    if "freq" in fragments:
        chunks.append(f"{fragments['freq']}MHz")
    if "current" in fragments:
        chunks.append(f"{fragments['current']}A")
    if "power" in fragments:
        chunks.append(f"{fragments['power']}W")
    return " ".join(chunks)


async def _show_start(target, state: FSMContext, edit: bool = True) -> None:
    """Экран start: меню конструктора (state уже очищен вызывающим)."""
    await state.set_state(BuilderState.start)
    await _reply(target, START_TEXT, kb_start(), edit=edit)


async def _show_choosing_size(target, state: FSMContext, edit: bool = True) -> None:
    """Выбор размера борта: размеры квадро-рецептов + кнопка Hex (гексакоптер).

    Hex — не размер, а тип рамы: все гекса-сборки (рецепты с «Hex»/«Гекса»
    в названии, сейчас HEXA 4320, 15″) живут только под кнопкой Hex.
    """
    recipes = await list_recipes(active_only=True)
    sizes: list[int | str] = sorted(
        {int(r["size"]) for r in recipes if not _is_hexa_recipe(r)}
    )
    if any(_is_hexa_recipe(r) for r in recipes):
        sizes.append("hex")
    await state.set_state(BuilderState.choosing_size)
    await _reply(target, SIZE_TEXT, kb_size_menu(sizes), edit=edit)


# Гекса — тип рамы (шесть лучей), а не размер борта: отделяем гекса-рецепты
# и гекса-рамы от обычных сборок того же размера.
_HEXA_TOKENS = ("гекс", "hex")
# Реальный размер борта гекса-сборки (кнопка Hex) — рамы-гекса сейчас 15″.
HEXA_BUILD_SIZE = 15


def _is_hexa_recipe(recipe: dict) -> bool:
    """Гекса-рецепт: «Hex»/«Гекса» в заголовке (сейчас HEXA 4320 Hexacopter)."""
    title = str(recipe.get("title") or "").lower()
    return any(token in title for token in _HEXA_TOKENS)


def _is_hexa_frame(part: dict) -> bool:
    """Гекса-рама: params["hexa"] (синтетика) или маркер в названии."""
    if (part.get("params") or {}).get("hexa"):
        return True
    name = str(part.get("name") or "").lower()
    return any(token in name for token in _HEXA_TOKENS)


async def _show_recipe_list(target, state: FSMContext, edit: bool = True) -> None:
    """Список рецептов: «{title} — можно собрать: N шт» (L4 по остаткам).

    Hex-режим (data["hexa"]) — только гекса-рецепты; обычный — только квадро.
    """
    data = await state.get_data()
    size = int(data["size"])
    hexa = bool(data.get("hexa"))
    stock = {p["key"]: int(p.get("quantity") or 0) for p in await list_parts()}
    recipes = []
    for recipe in await list_recipes(size=size, active_only=True):
        if _is_hexa_recipe(recipe) != hexa:
            continue
        builds = recipe_builds(
            recipe.get("slots") or [], stock, str(recipe.get("title") or "")
        )
        recipes.append({**recipe, "builds": builds})
    await state.set_state(BuilderState.recipe_list)
    header = (
        "🚁 Гексакоптеры (Hex) — серийные конфигурации:"
        if hexa
        else f"📦 Серийные конфигурации {size}″ — можно собрать из остатков:"
    )
    await _reply(target, header, kb_recipe_list(recipes), edit=edit)


# Слоты-«производственный пример»: в рецепте стоит то, что серийно ставим,
# но деталь заменяема (камеры универсальны, vtx/rx ограничены только частотой).
EXAMPLE_SLOT_CATEGORIES = frozenset({"camera", "vtx", "rx"})

# Этап 11в (A2/B3): рама 6/8-моторных сборок — статус «под заказ», БЕЗ
# дюймовой привязки; эта строка — в карточке гекса-рецепта, slot_menu и review.
MTO_FRAME_LINE = "➖ Рама: под заказ (нет на складе)"

# Экран слота frame свободного режима при 6/8 моторах (B1): складские рамы —
# все квадро (4 луча), поэтому список складских рам НЕ показывается вовсе.
FRAME_MTO_TEXT = (
    "Рамы для конфигураций на 6/8 моторов на складе нет.\n"
    "➖ Оформить раму под заказ"
)


def _recipe_card_text(recipe: dict, parts_by_key: dict) -> str:
    """Карточка рецепта: состав по слотам человекочитаемо (label + имя + кол-во).

    Под camera/vtx/rx — пометка «производственный пример — заменяемо»;
    рамы в рецепте нет — в конце строка «Рама: любая {size}″ — выбирается
    на шаге оформления» (модель "size": рама подбирается по размеру борта).
    Гекса-рецепт — исключение: «Гексакоптер» это отдельная рама (шесть
    лучей), а не любая рама размера борта — показываем её явным именем.
    """
    size = int(recipe["size"])
    lines = [f"📦 {recipe['title']} · {size}″"]
    slot_by_cat = {s.get("category"): s for s in recipe.get("slots") or []}
    for category, _required, _title in BUILD_SLOTS:
        slot = slot_by_cat.get(category)
        if category == "fc_esc_stack" and slot is None:
            fc_slot = slot_by_cat.get("fc")
            esc_slot = slot_by_cat.get("esc")
            if fc_slot and esc_slot:
                # Раздельная пара FC+ESC закрывает слот стека (рецепты
                # 13″/15″, Этап 10в) — одна строка вместо двух слотов.
                fc_name = str(fc_slot.get("name") or fc_slot.get("part_key") or "—")
                esc_name = str(esc_slot.get("name") or esc_slot.get("part_key") or "—")
                lines.append(f"{label(category)}: {trunc_label(fc_name)} + {trunc_label(esc_name)}")
            continue
        if not slot:
            continue
        key = slot.get("part_key")
        qty = int(slot.get("qty") or 0)
        part = parts_by_key.get(key) if key else None
        # неразрешённый слот (имени нет на складе) — показываем имя из рецепта
        name = str(part.get("name")) if part else str(slot.get("name") or key or "—")
        line = f"{label(category)}: {trunc_label(name)} · {fmt_qty(qty, (part or {}).get('unit') or 'шт')}"
        if category in EXAMPLE_SLOT_CATEGORIES:
            line += " (производственный пример — заменяемо)"
        params = fmt_params((part or {}).get("params"))
        if params:
            line += f" · {params}"
        lines.append(line)
    if _is_hexa_recipe(recipe):
        # Этап 11в: рамы в рецепте гексы НЕТ — 6-моторная рама отдельное
        # изделие «под заказ», без дюймовой привязки (никаких «Рама 15»).
        line = MTO_FRAME_LINE
    else:
        line = f"Рама: любая {size}″ — выбирается на шаге оформления"
    lines.append(line)
    return "\n".join(lines)


async def _show_recipe_card(target, state: FSMContext, recipe: dict, edit: bool = True) -> None:
    """Карточка рецепта: состав + [💾 В черновик, ⬅ Назад]."""
    slots = recipe.get("slots") or []
    keys = {s.get("part_key") for s in slots if s.get("part_key")}
    parts_by_key = {p["key"]: p for p in await list_parts() if p["key"] in keys}
    await state.set_state(BuilderState.recipe_card)
    await _reply(target, _recipe_card_text(recipe, parts_by_key), kb_recipe(int(recipe["id"])), edit=edit)


async def _show_part_list(target, state: FSMContext, edit: bool = True) -> None:
    """Список кандидатов текущего слота: apply_slot_filters → filter_parts → страница.

    Фильтр ПО МОДЕЛИ СОВМЕСТИМОСТИ (Этап 10, свободный режим):
      "recipe"    — только allowed_union(size) (L1); union пуст → «этот узел
                    собирается иначе» + кнопка в серийный режим;
      "freq"      — пересечение диапазона/частоты уже отфильтровал matcher;
      "size"      — params["size"] == size; запчасти — и без размера (как
                    раньше в matcher); рама — ТОЛЬКО с совпадающим размером,
                    рамы без распознанного размера скрыты (хотфикс Д2);
      "universal" — все детали категории с остатком > 0 + universal_note
                    в шапке (камеры/прочее подходят любому дрону).
    """
    data = await state.get_data()
    slot = data.get("current_slot")
    if not slot:
        await _show_slot_menu(target, state)
        return
    slots = data.get("slots") or {}
    chosen_parts = await _load_chosen_parts(slots)
    mode = data.get("mode")
    size = data.get("size")
    motor_count = data.get("motor_count")
    if mode == "free" and slot == "frame" and motor_count and int(motor_count) > 4:
        # Этап 11в (B1): 6/8-моторные рамы на складе не бывают (складские —
        # все квадро, 4 луча) → список складских рам не показываем, рама
        # оформляется «под заказ» (mto-слот: key=None, made_to_order=True).
        await state.set_state(BuilderState.part_list)
        await _reply(target, FRAME_MTO_TEXT, kb_frame_mto(), edit=edit)
        return
    candidates = apply_slot_filters(await list_parts(category=slot), slot, chosen_parts)
    fragments = data.get("filters") or {}
    if fragments:
        candidates = filter_parts(candidates, fragments)
    note: str | None = None
    if mode == "free" and size:
        model = model_of(slot)
        if model == "recipe":
            # Этап 11в (C2): whitelist учитывает пару (размер, число моторов):
            # квадро 15″ больше не предлагает 4320, гекса 15″ — предлагает.
            union = allowed_union(
                await list_recipes(active_only=True),
                int(motor_count) if motor_count else None,
            )
            allowed = union.get(int(size), {}).get(slot) or set()
            if not allowed:
                await state.set_state(BuilderState.part_list)
                await _reply(
                    target,
                    f"Для {int(size)}″ этот узел собирается иначе — в серийные "
                    "сборки он входит только в готовых конфигурациях.\n"
                    "Выберите готовый состав: 📦 Серийные конфигурации.",
                    kb_part_list([], 0, 1, slot, serial_hint=True),
                    edit=edit,
                )
                return
            candidates = [p for p in candidates if p["key"] in allowed]
        elif model == "size":
            if slot == "frame":
                # Хотфикс Д2: рамы без распознанного размера скрыты из
                # подбора свободного режима (запчасти — по-прежнему видны).
                # 6/8-моторные сборки сюда не доходят — экран mto выше.
                candidates = [
                    p
                    for p in candidates
                    if (p.get("params") or {}).get("size") is not None
                    and int((p.get("params") or {}).get("size")) == int(size)
                ]
            else:
                candidates = [
                    p
                    for p in candidates
                    if (p.get("params") or {}).get("size") is None
                    or int((p.get("params") or {}).get("size")) == int(size)
                ]
        # "freq": matcher уже сузил по пересечению с выбранным трактом;
        # "universal": без фильтра — подходит любому дрону.
        if model == "universal":
            note = universal_note(slot)
    total = len(candidates)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(int(data.get("page") or 0), 0), pages - 1)
    await state.update_data(page=page)
    page_parts = candidates[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    text = f"{label(slot)} — выберите деталь (стр. {page + 1} из {pages}, найдено {total}):"
    if note:
        text = f"💡 {label(slot)} — {note}.\n" + text
    if fragments:
        text += f"\nФильтр: {_fragments_repr(fragments)}"
    await state.set_state(BuilderState.part_list)
    await _reply(target, text, kb_part_list(page_parts, page, pages, slot), edit=edit)


def _review_text(result: dict, slots: dict | None = None, parts_by_slot: dict | None = None) -> str:
    """Экран проверки: состав сборки по слотам + статус (только человеческие строки)."""
    slots = slots or {}
    parts_by_slot = parts_by_slot or {}
    lines: list[str] = ["📋 Проверка сборки"]
    build_size = result.get("build_size")
    if build_size:
        lines[-1] += f" {int(build_size)}″"
    for category, chosen in slots.items():
        if category == "frame" and (chosen or {}).get("made_to_order"):
            # Этап 11в (B3): рама «под заказ» — mto-слот, детали нет.
            lines.append(MTO_FRAME_LINE)
            continue
        part = parts_by_slot.get(category)
        if not part:
            continue
        name = str(part.get("name") or "—")
        line = f"{label(category)}: {name} · {fmt_qty(part.get('quantity'), part.get('unit') or 'шт')}"
        params = fmt_params(part.get("params"))
        if params:
            line += f" · {params}"
        lines.append(line)
    if result.get("ok"):
        lines.append("✅ Всё совместимо, на складе хватает.")
        return "\n".join(lines)
    missing = result.get("missing") or []
    if missing:
        lines.append("⚠️ Не выбрано: " + ", ".join(label(cat) for cat in missing))
    errors = result.get("errors") or []
    if errors:
        lines.append("❌ Проблемы:")
        lines.extend(f"— {error}" for error in errors)
    warnings = result.get("warnings") or []
    if warnings:
        lines.append("💡 Советы:")
        lines.extend(f"— {warning}" for warning in warnings)
    return "\n".join(lines)
# ============================================================
# хендлеры: старт и меню слотов
# ============================================================
async def cmd_builder(message: types.Message, state: FSMContext) -> None:
    """/builder → стартовое меню конструктора (новая FSM-сессия)."""
    await state.clear()
    await state.update_data(slots={}, draft_id=None, current_slot=None, page=0, filters={})
    await _show_start(message, state, edit=False)


async def cb_mode_free(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:mode:free → свободная сборка: выбор размера борта (Этап 10)."""

    await safe_answer(callback)
    await state.update_data(
        slots={}, draft_id=None, current_slot=None, page=0, filters={},
        mode="free", size=None, hexa=False, motor_count=None,
    )
    await _show_choosing_size(callback.message, state)


async def cb_mode_recipe(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:mode:recipe → серийный режим: выбор размера борта (Этап 10)."""

    await safe_answer(callback)
    await state.update_data(
        slots={}, draft_id=None, current_slot=None, page=0, filters={},
        mode="recipe", size=None, hexa=False, motor_count=None,
    )
    await _show_choosing_size(callback.message, state)


async def cb_size(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:size:<n> | b:size:hex → серийный: список рецептов; свободный: меню слотов.

    b:size:hex — гексакоптер (тип рамы): под капотом размер 15″ + hexa=True,
    список рецептов фильтруется по гекса-принадлежности; в свободном режиме
    Hex = 6 моторов (рама — «под заказ», Этап 11в).
    Свободный режим с размером, у которого серийные сборки с разным числом
    моторов (сейчас 15″: квадро ×4 и гекса ×6), — экран выбора моторов
    b:motors:<n> (Этап 11в): whitelist L1 и рамы зависят от этой пары.
    """

    await safe_answer(callback)
    raw = (callback.data or "").rsplit(":", 1)[1]
    if raw == "hex":
        size, hexa = HEXA_BUILD_SIZE, True
    else:
        try:
            size, hexa = int(raw), False
        except ValueError:
            await safe_answer(callback, "Неизвестный размер", show_alert=True)
            return
    await state.update_data(size=size, hexa=hexa, page=0, filters={}, current_slot=None)
    if (await state.get_data()).get("mode") == "recipe":
        await _show_recipe_list(callback.message, state)
        return
    if hexa:
        # Свободный Hex: 6 моторов, рама — «под заказ» (B1/B3).
        await state.update_data(motor_count=6)
        await _show_slot_menu(callback.message, state)
    else:
        counts = sorted(
            {
                recipe_motor_qty(r)
                for r in await list_recipes(size=size, active_only=True)
            }
        )
        if len(counts) > 1:
            await state.set_state(BuilderState.choosing_size)
            await _reply(
                callback.message, MOTOR_COUNT_TEXT, kb_motor_count(counts)
            )
        else:
            await state.update_data(motor_count=counts[0] if counts else None)
            await _show_slot_menu(callback.message, state)


async def cb_motor_count(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:motors:<n> → число моторов свободной сборки (Этап 11в) → меню слотов."""

    await safe_answer(callback)
    raw = (callback.data or "").rsplit(":", 1)[1]
    try:
        count = int(raw)
    except ValueError:
        await safe_answer(callback, "Неизвестная конфигурация", show_alert=True)
        return
    await state.update_data(motor_count=count, page=0, filters={}, current_slot=None)
    await _show_slot_menu(callback.message, state)


async def cb_mto_frame(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:mto_frame → рама «под заказ» (Этап 11в): slots["frame"] = mto-слот.

    {"key": None, "made_to_order": True}: строка «➖ Рама: под заказ (нет на
    складе)» в slot_menu/review; слот НЕ участвует в L1/L3/L4, complete=True.
    """

    await safe_answer(callback)
    data = await state.get_data()
    slots = data.get("slots") or {}
    slots["frame"] = {"key": None, "made_to_order": True}
    await state.update_data(slots=slots, current_slot=None)
    await _show_slot_menu(callback.message, state)
    await safe_answer(callback, "Рама оформлена под заказ")


async def cb_recipe(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:recipe:<id> → карточка рецепта (состав по слотам)."""

    await safe_answer(callback)
    try:
        recipe_id = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        await safe_answer(callback, "Рецепт не найден", show_alert=True)
        return
    recipe = await get_recipe(recipe_id)
    if recipe is None:
        await safe_answer(callback, "Рецепт не найден", show_alert=True)
        await _show_recipe_list(callback.message, state)
        return
    await _show_recipe_card(callback.message, state, recipe)


async def cb_recipe_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:recipe:back → из карточки рецепта к списку рецептов размера."""

    await safe_answer(callback)
    await _show_recipe_list(callback.message, state)


async def cb_recipe_build(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:recipe:build:<id> → slots из рецепта → меню слотов (сборка по рецепту).

    Слоты предзаполняются составом рецепта, mode="recipe" сохраняется;
    рамы в рецепте нет — слот frame остаётся пустым, рама выбирается по
    размеру борта как в свободном режиме (модель "size").
    Гекса-рецепт (Этап 11в): слот frame становится mto («под заказ») —
    6-моторная рама отдельное изделие, в слот деталью не выбирается.
    motor_count берётся из qty слота motor рецепта — L1-union в review
    считается по паре (размер, число моторов).
    """

    await safe_answer(callback)
    try:
        recipe_id = int((callback.data or "").rsplit(":", 1)[1])
    except ValueError:
        await safe_answer(callback, "Рецепт не найден", show_alert=True)
        return
    recipe = await get_recipe(recipe_id)
    if recipe is None:
        await safe_answer(callback, "Рецепт не найден", show_alert=True)
        await _show_recipe_list(callback.message, state)
        return
    # DB-формат slots — список [{category, part_key, name, qty}] → формат черновика
    # (легаси-категории battery/tx/антенны/VRX/payload отфильтровываются —
    # хотфиксы Этапов 10а/10б; рецепты в БД могли сидироваться с антенной).
    slots = {
        s["category"]: {"key": s["part_key"], "qty": int(s.get("qty") or 1)}
        for s in recipe.get("slots") or []
        if s.get("part_key") and s.get("category") in _BUILD_SLOT_CATEGORIES
    }
    if _is_hexa_recipe(recipe):
        # Этап 11в: рама гексы — отдельное изделие «под заказ», в слот не
        # выбирается (mto-слот: key=None, made_to_order=True; L1/L3/L4 мимо).
        slots["frame"] = {"key": None, "made_to_order": True}
    await state.update_data(
        slots=slots,
        current_slot=None,
        page=0,
        filters={},
        motor_count=recipe_motor_qty(recipe),
    )
    await _show_slot_menu(callback.message, state)
    await safe_answer(callback, "Рецепт загружен в сборку")


async def cb_start_resume(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:start:resume → свежий черновик пользователя → меню слотов (slots из config)."""

    await safe_answer(callback)
    drafts = await list_drafts(int(callback.from_user.id))
    if not drafts:
        await safe_answer(callback, "Черновиков пока нет", show_alert=True)
        return
    draft = drafts[0]  # list_drafts возвращает свежие первыми
    config = draft.get("config") or {}
    await state.update_data(
        slots=_slots_in_build(config.get("slots")),
        draft_id=int(draft["id"]),
        current_slot=None,
        page=0,
        filters={},
    )
    await _show_slot_menu(callback.message, state)


async def cb_start_drafts(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:start:drafts → список черновиков пользователя."""

    await safe_answer(callback)
    await state.update_data(slots={}, draft_id=None, current_slot=None, page=0, filters={})
    await _show_drafts(callback.message, state, callback.from_user.id)


# ============================================================
# хендлеры: выбор слота и детали, пагинация, поиск
# ============================================================
async def cb_slot(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:slot:<category> → current_slot, page=0, filters={} → список кандидатов."""

    await safe_answer(callback)
    category = (callback.data or "").split(":", 2)[2]
    if category not in {cat for cat, _required, _title in BUILD_SLOTS}:
        await safe_answer(callback, "Неизвестный раздел", show_alert=True)
        return
    await state.update_data(current_slot=category, page=0, filters={})
    await _show_part_list(callback.message, state)


async def cb_part(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:part:<id> → get_part_by_id(id) → slots[current_slot]={"key": part["key"],
    "qty": 1} → меню слотов.

    В callback_data — int parts.id (схема Этапа 9а: ключи-имена длинных
    деталей превышали лимит Telegram 64 байта на callback_data); в state
    и черновиках остаются КЛЮЧИ-имена (на них держатся алиасы и
    реклассификация). part is None (старая закэшированная кнопка с
    ключом-именем из прошлых сообщений либо удалённая деталь) → alert
    «Список устарел…» + перерендер part_list.
    """

    await safe_answer(callback)
    data = await state.get_data()
    slot = data.get("current_slot")
    if not slot:
        await _show_slot_menu(callback.message, state)
        return
    try:
        part_id = int((callback.data or "").split(":", 2)[2])
    except ValueError:
        part_id = 0  # не число (кнопка до Этапа 9а с ключом-именем) → детали нет
    part = await get_part_by_id(part_id)
    if part is None:
        await safe_answer(callback, "Список устарел, обновляю…", show_alert=True)
        await _show_part_list(callback.message, state)
        return
    slots = dict(data.get("slots") or {})
    slots[slot] = {"key": part["key"], "qty": 1}
    await state.update_data(slots=slots)
    await _show_slot_menu(callback.message, state)


# ============================================================
# хендлеры: текстовый поиск part_input
# ============================================================
async def process_part_input(message: types.Message, state: FSMContext) -> None:
    """Текст в part_input → matcher.parse_fragments → filters → part_list (стр 0)."""
    fragments = parse_fragments(message.text or "")
    await state.update_data(filters=fragments, page=0)
    await _show_part_list(message, state, edit=False)


async def process_part_input_not_text(message: types.Message) -> None:
    """Не-текст (фото и т.п.) в part_input → подсказка, состояние не меняем."""
    await message.answer("Отправьте запрос текстом, например «10 3115 900KV».")


# ============================================================
# хендлеры: итог, сохранение, финиш, отмена, назад
# ============================================================
async def cb_review(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:review → экран итога (check_compatibility, Этап 5)."""

    await safe_answer(callback)
    await _show_review(callback.message, state)


async def cb_save(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:save → save_draft(...) → подтверждение (черновик обновляется при resume)."""

    await safe_answer(callback)
    data = await state.get_data()
    slots = _slots_in_build(data.get("slots"))  # легаси battery/tx не сохраняем
    name = f"Черновик {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    draft_id = data.get("draft_id")
    draft = await save_draft(
        int(callback.from_user.id),
        {"slots": slots},
        name=name,
        draft_id=int(draft_id) if draft_id else None,
    )
    await state.update_data(draft_id=int(draft["id"]))
    await safe_answer(callback, "💾 Черновик сохранён")


async def cb_done(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:done → FSM завершена: state.clear() → прощание."""

    await safe_answer(callback)
    await state.clear()
    await callback.message.answer(
        "Готово! Черновик сохранён — вернусь к нему через «📂 Продолжить черновик»."
    )


async def cb_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:cancel → очистить state → экран start."""

    await safe_answer(callback)
    await state.clear()
    await state.update_data(slots={}, draft_id=None, current_slot=None, page=0, filters={})
    await _show_start(callback.message, state)
    await safe_answer(callback, "Вышли из конструктора. Возврат: /builder")


async def cb_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:back → уровень вверх: drafts → start; recipe_card/recipe_list → выбор
    размера; part_list/review → меню слотов."""

    await safe_answer(callback)
    current = await state.get_state()
    if current == BuilderState.drafts.state:
        await _show_start(callback.message, state)
    elif current in (
        BuilderState.choosing_size.state,
        BuilderState.recipe_list.state,
        BuilderState.recipe_card.state,
    ):
        await _show_choosing_size(callback.message, state)
    else:
        await _show_slot_menu(callback.message, state)


# ============================================================
# хендлеры: черновики
# ============================================================
async def cb_draft_load(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:draft:load:<id> → get_draft(id, user_id) → slots из config → меню слотов."""

    await safe_answer(callback)
    draft_id = int((callback.data or "").rsplit(":", 1)[1])
    draft = await get_draft(draft_id, int(callback.from_user.id))
    if draft is None:
        await safe_answer(callback, "Черновик не найден", show_alert=True)
        await _show_drafts(callback.message, state, callback.from_user.id)
        return
    config = draft.get("config") or {}
    await state.update_data(
        slots=_slots_in_build(config.get("slots")),
        draft_id=draft_id,
        current_slot=None,
        page=0,
        filters={},
    )
    await _show_slot_menu(callback.message, state)


async def cb_draft_del(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:draft:del:<id> → delete_draft(id, user_id) → ре-рендер списка черновиков."""

    await safe_answer(callback)
    draft_id = int((callback.data or "").rsplit(":", 1)[1])
    deleted = await delete_draft(draft_id, int(callback.from_user.id))
    if not deleted:
        await safe_answer(callback, "Черновик не найден", show_alert=True)
    await _show_drafts(callback.message, state, callback.from_user.id)


async def cb_page(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:page:+1/-1 → страница ±1 (границы зажимает _show_part_list)."""

    await safe_answer(callback)
    delta = 1 if callback.data == "b:page:+1" else -1
    data = await state.get_data()
    await state.update_data(page=max(0, int(data.get("page") or 0) + delta))
    await _show_part_list(callback.message, state)


async def cb_input(callback: types.CallbackQuery, state: FSMContext) -> None:
    """b:input → part_input: ждём текст запроса (parse_fragments на следующем шаге)."""

    await safe_answer(callback)
    data = await state.get_data()
    slot = data.get("current_slot")
    title = label(slot) if slot else "детали"
    example = ""
    if slot:
        parts = await list_parts(category=slot)
        if parts:
            example = str(parts[0].get("name") or "")
            if len(example) > 30:
                example = example[:29] + "…"
    lines = [f"Напишите, что ищем среди «{title}»:"]
    if example:
        lines.append(f"например «{example}».")
    else:
        lines.append("например «10 дюймов».")
    lines.append("Примеры: «MARK4», «10 дюймов», «5,8 ГГц». Отмена — /cancel")
    await state.set_state(BuilderState.part_input)
    try:
        await callback.message.edit_text("\n".join(lines), reply_markup=None)
    except Exception as e:  # noqa: BLE001
        system_logger.warning(f"Builder input edit failed: {e}")



async def _show_review(target, state: FSMContext) -> None:
    """Экран итога. ПОРЯДОК ПРОВЕРОК (Этап 10): L1 whitelist рецептов
    (только категории "recipe") → L2 форма FC+ESC → L3 параметрические
    правила (compat, Этап 5) → L4 наличие (расход на дрон: motor ×4,
    остальные слоты ×1). Все строки — человеческие."""
    data = await state.get_data()
    slots = _slots_in_build(data.get("slots"))  # легаси battery/tx — мимо (10а)
    size = data.get("size")
    motor_count = data.get("motor_count")
    config = {"slots": slots}
    parts_by_slot = await _load_chosen_parts(slots)
    rules = await list_compat_rules(active_only=True)
    # L4: расход на дрон подставляется в конфиг перед проверкой остатков —
    # моторов нужно motor_count (свободный режим 11в: 4 или 6; рецепты —
    # qty слота motor), остальные слоты по 1 (recipes.py).
    consumption = drone_consumption(config, parts_by_slot, motor_count)
    config_l4 = {
        # Хотфикс Д6: явный размер сборки (из state) в конфиг — иначе compat
        # выводит размер из РАМЫ, и рама «не своего» размера никогда не даёт
        # ошибку (кейс владельца: рама Mark4 = 10″ на борта 15″ — тишина).
        # None → прежнее поведение (выведение из слотов).
        "size": size,
        "slots": {
            category: {**slot, "qty": consumption.get(category, 1)}
            for category, slot in slots.items()
        }
    }
    result = check_compatibility(config_l4, parts_by_slot, rules)
    errors: list[str] = list(result.get("errors") or [])
    warnings: list[str] = list(result.get("warnings") or [])
    if size:
        union = allowed_union(
            await list_recipes(active_only=True),
            int(motor_count) if motor_count else None,
        )
        wl_errors = whitelist_errors(config, int(size), union)
        if wl_errors:
            errors = wl_errors + errors  # L1 раньше остальных
        shape_errors = fc_esc_shape_errors(config, int(size))
        if shape_errors:
            errors = errors + shape_errors  # L2 после L1, перед L3/L4
    await state.set_state(BuilderState.review)
    await _reply(
        target,
        _review_text({**result, "errors": errors, "warnings": warnings}, slots, parts_by_slot),
        kb_review(result),
    )


def _drafts_text(drafts: list) -> str:
    """Список черновиков: «• имя — дата, заполнено X из N» (человеческие строки)."""
    if not drafts:
        return "🗂 Ваши черновики:\n\nЧерновиков пока нет. Начните: 🆕 Новая сборка"
    lines = ["🗂 Ваши черновики:", ""]
    for draft in drafts:
        updated = str(draft.get("updated_at") or "")[:10]  # YYYY-MM-DD
        parts = updated.split("-")
        date = ".".join(reversed(parts)) if len(parts) == 3 else updated
        # Заполненность: слоты BUILD_SLOTS; раздельная пара fc+esc (Этап 10в)
        # закрывает слот стека — считается как один заполненный слот.
        config_cats = {
            c
            for c in ((draft.get("config") or {}).get("slots") or {})
            if c in _BUILD_SLOT_CATEGORIES
        }
        covered = config_cats & _MENU_SLOT_CATEGORIES
        if {"fc", "esc"} <= config_cats:
            covered |= {"fc_esc_stack"}
        lines.append(
            f"• {draft.get('name')} — {date}, заполнено {len(covered)} из {len(BUILD_SLOTS)}"
        )
    return "\n".join(lines)


async def _show_drafts(target, state: FSMContext, user_id: int) -> None:
    drafts = await list_drafts(int(user_id))
    await state.set_state(BuilderState.drafts)
    await _reply(target, _drafts_text(drafts), kb_drafts(drafts))


def _slot_menu_text(slots: dict, chosen_parts: dict, build_size) -> str:
    """Меню сборки: состав по всем слотам BUILD_SLOTS (подписи — человеческие)."""
    header = "🛠 Сборка" + (f" {int(build_size)}″" if build_size else "") + " — состав:"
    lines = [header]
    for category, _required, _title in BUILD_SLOTS:
        part = chosen_parts.get(category)
        if category == "frame" and (slots.get("frame") or {}).get("made_to_order"):
            # Этап 11в (B3): рама «под заказ» — mto-слот без детали.
            lines.append(MTO_FRAME_LINE)
            continue
        if category == "fc_esc_stack" and part is None:
            # Раздельная пара FC+ESC закрывает слот стека (рецепты 13″/15″,
            # Этап 10в) — одна строка, слот считается выбранным.
            fc_part = chosen_parts.get("fc")
            esc_part = chosen_parts.get("esc")
            if fc_part and esc_part:
                lines.append(
                    f"✅ {label('fc_esc_stack')}: "
                    f"{fc_part.get('name')} + {esc_part.get('name')}"
                )
                continue
        lines.append(slot_status_line(category, part))
    return "\n".join(lines)


async def _show_slot_menu(target, state: FSMContext) -> None:
    data = await state.get_data()
    slots = data.get("slots") or {}
    chosen_parts = await _load_chosen_parts(slots)
    build_size = derive_build_size({"slots": slots}, chosen_parts)
    await state.set_state(BuilderState.slot_menu)
    await _reply(target, _slot_menu_text(slots, chosen_parts, build_size), kb_slot_menu(slots, build_size))

# Callback-префиксы, обрабатываемые этим роутером (фиксация Шага 1 Этапа 6).
# «Висячих» нет: совокупность callback_data клавиатур keyboards/builder.py
# покрывается (кроме "dummy" — глобальный no-op handlers/menu.py); smoke-тест
# tests/test_builder_handlers.py проверяет покрытие через handler.check().
CALLBACK_PREFIXES: frozenset[str] = frozenset(
    {
        "b:mode:recipe",
        "b:mode:free",
        "b:start:resume",
        "b:start:drafts",
        "b:size:",
        "b:motors:",
        "b:mto_frame",
        "b:recipe:",
        "b:recipe:build:",
        "b:recipe:back",
        "b:slot:",
        "b:part:",
        "b:page:+1",
        "b:page:-1",
        "b:input",
        "b:back",
        "b:review",
        "b:save",
        "b:done",
        "b:cancel",
        "b:draft:load:",
        "b:draft:del:",
    }
)

START_TEXT = (
    "🛠 Конструктор дронов\n\n"
    "📦 Серийные конфигурации — готовые производственные сборки: "
    "выбираете борт, смотрите состав и сколько можно собрать из остатков.\n"
    "🛠 Свободная сборка — сами выбираете детали по слотам; покажу только "
    "проверенные сочетания.\n"
    "Показываю только то, что есть на складе."
)

SIZE_TEXT = "Выберите размер борта:"

MOTOR_COUNT_TEXT = "Выберите количество моторов:"


# ============================================================
# регистрация хендлеров (явная, без декораторов — соглашение проекта)
# ============================================================
router.message.register(cmd_builder, Command("builder"))

router.callback_query.register(cb_mode_recipe, F.data == "b:mode:recipe")
router.callback_query.register(cb_mode_free, F.data == "b:mode:free")
router.callback_query.register(cb_start_resume, F.data == "b:start:resume")
router.callback_query.register(cb_start_drafts, F.data == "b:start:drafts")
router.callback_query.register(cb_size, F.data.startswith("b:size:"))
router.callback_query.register(cb_motor_count, F.data.startswith("b:motors:"))
router.callback_query.register(cb_mto_frame, F.data == "b:mto_frame")
router.callback_query.register(cb_recipe_back, F.data == "b:recipe:back")
router.callback_query.register(cb_recipe_build, F.data.startswith("b:recipe:build:"))
router.callback_query.register(cb_recipe, F.data.startswith("b:recipe:"))
router.callback_query.register(cb_slot, F.data.startswith("b:slot:"))
router.callback_query.register(cb_part, F.data.startswith("b:part:"))
router.callback_query.register(cb_page, F.data.in_({"b:page:+1", "b:page:-1"}))
router.callback_query.register(cb_input, F.data == "b:input")
router.callback_query.register(cb_back, F.data == "b:back")
router.callback_query.register(cb_review, F.data == "b:review")
router.callback_query.register(cb_save, F.data == "b:save")
router.callback_query.register(cb_done, F.data == "b:done")
router.callback_query.register(cb_cancel, F.data == "b:cancel")
router.callback_query.register(cb_draft_load, F.data.startswith("b:draft:load:"))
router.callback_query.register(cb_draft_del, F.data.startswith("b:draft:del:"))

router.message.register(process_part_input, BuilderState.part_input, F.text)
router.message.register(process_part_input_not_text, BuilderState.part_input)

