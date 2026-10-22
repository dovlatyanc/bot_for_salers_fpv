"""E2E-поток «🛠 Конструктора дронов» на фейках (Этап 7).

Полный пользовательский сценарий через handlers/builder.py: /builder → новая
конфигурация → заполнение слотов part-колбэками → итог (compat, Этап 5) →
сохранение черновика → resume. Диспетчер Telegram не используется: хендлеры
вызывываются НАПРЯМУЮ с FakeMsg/FakeCb (tests/fakes.py), БД реальная — tmp
SQLite (conftest) + CSV-фикстура через import_stock_csv (формат выгрузки 1С,
cp1251, Этап 1). Тексты ассертов — фактические строки рендеров
handlers/builder.py (_slot_menu_text/_show_part_list/_review_text).

Сценарии (каждый — отдельный тест):
  1) happy_path_10              — полный цикл до «💾 Черновик сохранён»;
  2) resume_draft               — новый state → b:start:resume → слоты восстановлены;
  3) review_incomplete          — только рама → Некомплектно (vtx, camera…);
  4) legacy_draft_filtered      — легаси-черновик (антенна/VRX в slots) →
                                  при загрузке отфильтрованы (_slots_in_build);
  5) draft_update_not_duplicate — повторный b:save обновляет, не дублирует;
  + доборные flow-шаги: текстовый фильтр part_input, пагинация + b:back,
    финал b:done после сохранения;
  + Этап 10в: рецепт-гексакоптер 15″ (6 моторов, два ESC) — раздельная
    пара fc+esc закрывает слот стека (карточка/меню/review/черновик).
  + хотфикс (пред-Этап-11): явный размер сборки против рамы Mark4 в итоге
    (Д2+Д6), рамы без распознанного размера скрыты из подбора (Д2).
"""

from data.builder.categories import BUILD_SLOTS
from db import get_sessionmaker
from db.repositories import (
    get_part,
    list_drafts,
    list_parts,
    list_recipes,
    save_draft,
    seed_recipes,
)
from db.stock_import import import_stock_csv, part_key
from handlers import builder
from keyboards.builder import trunc_label
from states.states import BuilderState
from tests.fakes import FakeCb, FakeMsg, kb_data, make_state

# Обязательные слоты хорошего 10″-борта (имена из CSV-фикстуры ниже).
# Хотфиксы 10а/10б: TX и антенны больше не слоты борта — в конфиг не входят.
FULL_CONFIG: dict[str, str] = {
    "frame": "Рама 10 МАРК4-V2",
    "motor": "Мотор axisfly 3115 900KV",
    "prop": "Пропеллер HQProp 10Х5Х3 (2CW+2CCW)",
    "fc_esc_stack": (
        "Полетный контроллер Axisflying ECO F405 "
        "+ регулятор оборотов Axisflying Argus ECO 60A"
    ),
    "vtx": "Видеопередатчик Axisflying 3W 5.8ГГц",
    "camera": "Камера FPV 1800 TVL",
    "rx": "Приемник axisfly (RX) 400MHz, Модель Generic C3 LR1121 400RX",
}

# Фикстура остатков (формат Этапа 1: «Номенклатура, ;Ед;Кол-во», cp1251).
# «RushFPV 3.3G …» — легаси-слот vrx для сценария 4 (черновик до хотфикса
# 10б); антенны и VRX — позиции склада вне слотов борта (хотфикс 10б), в
# конфигурацию не входят. Последние пять строк — имена слотов сид-рецептов
# 10″ (Этап 10): после перевысида рецептов они резолвятся в part_key и
# наполняют L1-union свободного режима.
CSV_ROWS: tuple[str, ...] = (
    *FULL_CONFIG.values(),
    "RushFPV 4.8G - 6.1G XSPAN VRX приёмник",
    "Патч антенна Maple Wireless 4.85G - 6.3G",
    "RushFPV 3.3G XSPAN VRX приёмный модуль",
    "Мотор для FPV BrotherHobby 3115 900 KV SE",
    "Пропеллеры нейлон gemfan 1050 2ccw+2cw",
    "Приемник (RX) 915MHz",
    "Антенна axisfly 5.8 GHz RHCP",
    "Камера Axisflying Firefly (Разъем 5-28V)",
)


async def _seed(tmp_path, rows=CSV_ROWS) -> None:
    path = tmp_path / "Остатки.csv"
    path.write_text(
        "".join(f"{name}, ;шт;50,000\r\n" for name in rows), encoding="cp1251"
    )
    await import_stock_csv(path)
    # Перевысид рецептов (прод-поток Этапа 10): остатки → дозаполнение part_key,
    # иначе сид conftest остаётся с part_key=None и L1-union пуст.
    async with get_sessionmaker()() as session:
        await seed_recipes(session)


async def fill_slot(state, cb_factory, category: str, key: str):
    """Прямой вызов cb-хендлера «b:part:<id>» для слота category (как нажатие
    кнопки детали в part_list); проверка возврата в slot_menu. id берётся из
    БД по ключу — в callback_data хранится int parts.id (схема Этапа 9а)."""
    part = await get_part(key)
    assert part is not None, f"деталь {key!r} не найдена в БД"
    await state.update_data(current_slot=category)
    await state.set_state(BuilderState.slot_menu)
    cb = cb_factory(f"b:part:{part['id']}")
    await builder.cb_part(cb, state)
    assert await state.get_state() == BuilderState.slot_menu
    return cb


async def _start_new(state):
    """«/builder» → «🛠 Свободная сборка» → экран размера (Этап 10) →
    «b:size:10» → slot_menu свободной сборки. Возвращает cb экрана slot_menu."""
    msg = FakeMsg("/builder")
    await builder.cmd_builder(msg, state)
    await builder.cb_mode_free(FakeCb("b:mode:free"), state)
    assert await state.get_state() == BuilderState.choosing_size
    cb = FakeCb("b:size:10")
    await builder.cb_size(cb, state)
    return cb


async def _fill_all(state) -> None:
    """Заполняет все обязательные слоты FULL_CONFIG (в порядке словаря)."""
    for slot, name in FULL_CONFIG.items():
        await fill_slot(state, FakeCb, slot, part_key(name))


# ============================================================
# 1) happy path: /builder → новая сборка 10″ → итог → черновик
# ============================================================

async def test_happy_path_10(tmp_path):
    await _seed(tmp_path)
    state = make_state()

    # /builder → стартовый экран: текст про конструктор + ровно 4 колбэка kb_start
    # (Этап 10: серийные конфигурации / свободная сборка / продолжить / мои черновики)
    msg = FakeMsg("/builder")
    await builder.cmd_builder(msg, state)
    start_text = msg.answers[-1][0]
    assert "Конструктор" in start_text
    start_cbs = {cb for _, cb in kb_data(msg.answers[-1][1]["reply_markup"])}
    assert start_cbs == {
        "b:mode:recipe", "b:mode:free", "b:start:resume", "b:start:drafts",
    }

    # b:mode:recipe → выбор размера (Этап 10) → b:size:10 → список рецептов 10″
    await builder.cb_mode_recipe(FakeCb("b:mode:recipe"), state)
    assert await state.get_state() == BuilderState.choosing_size
    await builder.cb_size(FakeCb("b:size:10"), state)
    assert await state.get_state() == BuilderState.recipe_list
    recipes = await list_recipes(size=10, active_only=True)
    assert len(recipes) == 5
    recipe = recipes[0]

    # b:recipe:<id> → карточка: «📦 {title} · 10″» + состав по слотам
    cb_card = FakeCb(f"b:recipe:{recipe['id']}")
    await builder.cb_recipe(cb_card, state)
    card_text = cb_card.message.edits[-1][0]
    assert await state.get_state() == BuilderState.recipe_card
    assert recipe["title"] in card_text and "10″" in card_text
    assert "Моторы:" in card_text and "Видеопередатчик (VTX):" in card_text
    # рама не входит в рецепт (модель "size") — строка выбора по размеру
    assert "Рама: любая 10″ — выбирается на шаге оформления" in card_text
    # camera/vtx/rx — производственный пример (заменяемо)
    assert "производственный пример — заменяемо" in card_text

    # b:recipe:build:<id> → слоты рецепта в сборку → меню слотов (7 «✅», мотор ×4)
    cb_use = FakeCb(f"b:recipe:build:{recipe['id']}")
    await builder.cb_recipe_build(cb_use, state)
    menu_text = cb_use.message.edits[-1][0]
    assert await state.get_state() == BuilderState.slot_menu
    data = await state.get_data()
    assert set(data["slots"]) == {
        "motor", "prop", "fc_esc_stack", "vtx", "camera", "rx",
    }
    assert data["slots"]["motor"]["qty"] == 4
    assert menu_text.count("✅") == 6

    # b:review → итог: без ❌ (рецепт — ground truth L1–L4); рамы в рецептах
    # нет (TX — не слот борта, хотфикс 10а) → «⚠️ Не выбрано», а не «Всё совместимо»
    cb_review = FakeCb("b:review")
    await builder.cb_review(cb_review, state)
    review_text = cb_review.message.edits[-1][0]
    assert await state.get_state() == BuilderState.review
    assert "❌" not in review_text
    assert "⚠️ Не выбрано" in review_text

    # b:save → «💾 Черновик сохранён», в БД ровно 1 черновик со слотами рецепта
    cb_save = FakeCb("b:save")
    await builder.cb_save(cb_save, state)
    assert cb_save.alerts and "сохранён" in (cb_save.alerts[-1][0] or "")
    drafts = await list_drafts(42)
    assert len(drafts) == 1
    assert drafts[0]["name"].startswith("Черновик ")
    assert set(drafts[0]["config"]["slots"]) == set(data["slots"])


# ============================================================
# 2) resume: свежий черновик → слоты восстановлены
# ============================================================

async def test_resume_draft(tmp_path):
    await _seed(tmp_path)
    state = make_state()
    await _start_new(state)
    await _fill_all(state)
    await builder.cb_save(FakeCb("b:save"), state)
    saved_id = (await list_drafts(42))[0]["id"]

    state2 = make_state()  # «новая сессия» пользователя
    await builder.cmd_builder(FakeMsg("/builder"), state2)
    cb_resume = FakeCb("b:start:resume")
    await builder.cb_start_resume(cb_resume, state2)

    menu_text = cb_resume.message.edits[-1][0]
    assert await state2.get_state() == BuilderState.slot_menu
    for name in FULL_CONFIG.values():
        assert name in menu_text, name
    data = await state2.get_data()
    assert data["slots"] == {
        slot: {"key": part_key(name), "qty": 1} for slot, name in FULL_CONFIG.items()
    }
    assert data["draft_id"] == saved_id


# ============================================================
# 3) некомплект: только рама
# ============================================================

async def test_review_incomplete(tmp_path):
    await _seed(tmp_path)
    state = make_state()
    await _start_new(state)
    await fill_slot(state, FakeCb, "frame", part_key(FULL_CONFIG["frame"]))

    cb_review = FakeCb("b:review")
    await builder.cb_review(cb_review, state)
    review_text = cb_review.message.edits[-1][0]

    assert "⚠️ Не выбрано" in review_text
    assert "Видеопередатчик (VTX)" in review_text and "FPV-камера" in review_text
    assert "Всё совместимо" not in review_text
    assert "Проверка сборки 10″" in review_text  # размер виден и на некомплекте
    # L1 (Этап 10, модель совместимости): рама — модель "size" и НИКОГДА
    # не даёт L1 (несоответствие размера ловит L3); «не применяется…»
    # в тексте быть не должно
    assert "не применяется в серийных сборках" not in review_text


# ============================================================
# 4) легаси-черновик (до хотфикса 10б): антенна/VRX в slots — при загрузке
#    в сборку отфильтровываются (_slots_in_build), меню показывает 7 слотов
# ============================================================

async def test_legacy_draft_slots_filtered_on_load(tmp_path):
    await _seed(tmp_path)
    legacy_slots = {
        **{
            slot: {"key": part_key(name), "qty": 1}
            for slot, name in FULL_CONFIG.items()
        },
        # слоты, выведенные из борта хотфиксом 10б (черновик старой сессии)
        "antenna_air": {"key": part_key("Антенна Pagoda.5,8 ГГц"), "qty": 1},
        "vrx": {"key": part_key("RushFPV 3.3G XSPAN VRX приёмный модуль"), "qty": 1},
    }
    await save_draft(42, {"slots": legacy_slots}, name="Легаси")

    state = make_state()
    await builder.cmd_builder(FakeMsg("/builder"), state)
    cb_resume = FakeCb("b:start:resume")
    await builder.cb_start_resume(cb_resume, state)

    assert await state.get_state() == BuilderState.slot_menu
    data = await state.get_data()
    assert "antenna_air" not in data["slots"] and "vrx" not in data["slots"]
    assert data["slots"] == {
        slot: {"key": part_key(name), "qty": 1} for slot, name in FULL_CONFIG.items()
    }
    menu_text = cb_resume.message.edits[-1][0]
    assert "Антенна бортовая" not in menu_text
    assert "Видеоприёмник (VRX)" not in menu_text


# ============================================================
# 5) повторное сохранение — update, а не дубль
# ============================================================

async def test_draft_update_not_duplicate(tmp_path):
    await _seed(tmp_path)
    state = make_state()
    await _start_new(state)
    await _fill_all(state)

    await builder.cb_save(FakeCb("b:save"), state)   # создал черновик
    first = await list_drafts(42)
    assert len(first) == 1

    await builder.cb_save(FakeCb("b:save"), state)   # b:save ещё раз — update
    drafts = await list_drafts(42)
    assert len(drafts) == 1
    assert drafts[0]["id"] == first[0]["id"]
    assert drafts[0]["config"]["slots"]["frame"]["key"] == part_key(FULL_CONFIG["frame"])


# ============================================================
# доборные flow-шаги: текстовый фильтр, пагинация+назад, финал
# ============================================================

async def test_text_filter_in_part_list(tmp_path):
    """b:input → текст «10 дюймов» в part_input → part_list с фильтром 10″."""
    await _seed(tmp_path)
    state = make_state()
    await _start_new(state)
    await builder.cb_slot(FakeCb("b:slot:prop"), state)

    await builder.cb_input(FakeCb("b:input"), state)
    assert await state.get_state() == BuilderState.part_input

    msg = FakeMsg("10 дюймов")
    await builder.process_part_input(msg, state)
    assert await state.get_state() == BuilderState.part_list
    filter_text, filter_kw = msg.answers[-1]
    assert "Фильтр: 10″" in filter_text
    assert "найдено 1" in filter_text
    # имена деталей — в label'ах кнопок клавиатуры part_list, не в тексте;
    # L1-union 10″ для prop — «Пропеллеры нейлон gemfan 1050 2ccw+2cw»
    # (HQProp 10Х5Х3 в серийных сборках не используется и отфильтрован)
    prop_labels = [
        label for label, cb in kb_data(filter_kw["reply_markup"])
        if cb.startswith("b:part:")
    ]
    assert any("gemfan 1050" in label for label in prop_labels)


async def test_pagination_and_back(tmp_path):
    """12 рам → part_list стр. 1 из 2 → b:page:+1 → стр. 2 из 2 → b:back → slot_menu."""
    # Хотфикс Д2: при известном размере сборки (в recipe-режиме это 10″ от
    # пропа рецепта) matcher скрывает рамы без распознанного размера и с
    # другим размером — поэтому у тестовых рам явный размер 10 в имени
    # («Рама 10 тестовая 01» → params["size"]=10); без него остались бы
    # только 2 рамы (МАРК4 + «…10») и пагинации не было бы.
    rows = (*CSV_ROWS, *(f"Рама 10 тестовая {i:02d}" for i in range(1, 12)))
    await _seed(tmp_path, rows)
    state = make_state()
    # серийный режим: рецепт 10″ → build → slot_menu; в recipe-режиме part_list
    # не ограничен L1-union (рама — модель "size", фильтр по размеру в свободном)
    recipes = await list_recipes(size=10, active_only=True)
    await builder.cb_mode_recipe(FakeCb("b:mode:recipe"), state)
    await builder.cb_size(FakeCb("b:size:10"), state)
    await builder.cb_recipe_build(FakeCb(f"b:recipe:build:{recipes[0]['id']}"), state)
    await builder.cb_slot(FakeCb("b:slot:frame"), state)

    cb_page = FakeCb("b:page:+1")
    await builder.cb_page(cb_page, state)
    assert "стр. 2 из 2" in cb_page.message.edits[-1][0]

    await builder.cb_back(FakeCb("b:back"), state)
    assert await state.get_state() == BuilderState.slot_menu


async def test_done_after_save(tmp_path):
    """b:save → b:done: прощание, FSM очищен, черновик остался в БД."""
    await _seed(tmp_path)
    state = make_state()
    await _start_new(state)
    await fill_slot(state, FakeCb, "frame", part_key(FULL_CONFIG["frame"]))
    await builder.cb_save(FakeCb("b:save"), state)

    cb_done = FakeCb("b:done")
    await builder.cb_done(cb_done, state)
    assert "Черновик сохранён" in cb_done.message.answers[-1][0]
    assert await state.get_state() is None
    assert len(await list_drafts(42)) == 1


# ============================================================
# Этап 10: серийный режим до «Всё совместимо»; фильтры по модели;
# L1 в review на форс-конфиге
# ============================================================

async def test_recipe_mode_build_review_ok(tmp_path):
    """Серийный режим 10″: карточка R1 → «Оформить сборку» → слоты рецепта
    (frame пуст, mode="recipe") → дозаполнение frame (+ замена rx) →
    review «Всё совместимо» (L1–L4 зелёные; TX — не слот борта, хотфикс 10а)."""
    await _seed(tmp_path)
    state = make_state()
    recipes = await list_recipes(size=10, active_only=True)
    recipe = next(r for r in recipes if r["title"].startswith("ECO F405"))

    await builder.cb_mode_recipe(FakeCb("b:mode:recipe"), state)
    await builder.cb_size(FakeCb("b:size:10"), state)
    await builder.cb_recipe(FakeCb(f"b:recipe:{recipe['id']}"), state)
    assert await state.get_state() == BuilderState.recipe_card
    await builder.cb_recipe_build(FakeCb(f"b:recipe:build:{recipe['id']}"), state)
    assert await state.get_state() == BuilderState.slot_menu
    data = await state.get_data()
    assert data["mode"] == "recipe" and data["size"] == 10
    assert "frame" not in data["slots"]                    # рамы в рецепте нет
    assert set(data["slots"]) == {
        "motor", "prop", "fc_esc_stack", "vtx", "camera", "rx",
    }

    # TX — не слот борта (хотфикс 10а) — не дозаполняется; RX рецепта
    # (915 МГц) меняем на 400 МГц, frame дозаполняем по размеру борта.
    await fill_slot(state, FakeCb, "rx", part_key(FULL_CONFIG["rx"]))
    await fill_slot(state, FakeCb, "frame", part_key(FULL_CONFIG["frame"]))

    cb_review = FakeCb("b:review")
    await builder.cb_review(cb_review, state)
    review_text = cb_review.message.edits[-1][0]
    assert "✅ Всё совместимо" in review_text
    assert "❌" not in review_text


async def test_free_mode_part_list_filters_by_model(tmp_path):
    """Свободный режим 10″: motor — только серийный BrotherHobby (5315 и
    axisfly 3115 вне union); camera — universal (тепловизор и дневная видны,
    в шапке пометка); frame — только params.size == 10."""
    rows = (
        *CSV_ROWS,
        "Мотор axisfly 5315 380KV",
        "Камера тепловизионная FPV 640",
        "Камера дневная FW",
        "Рама 13 дюймов",
        "Рама карбон",                    # хотфикс Д2: размер не распознан
    )
    await _seed(tmp_path, rows)
    state = make_state()
    await _start_new(state)                      # b:mode:free → b:size:10

    # motor: L1-union 10″ — только «Мотор для FPV BrotherHobby 3115 900 KV SE»
    cb_motor = FakeCb("b:slot:motor")
    await builder.cb_slot(cb_motor, state)
    motor_text, motor_kwargs = cb_motor.message.edits[-1]
    assert "найдено 1" in motor_text
    motor_labels = [
        label for label, cb in kb_data(motor_kwargs["reply_markup"])
        if cb.startswith("b:part:")
    ]
    assert any("BrotherHobby" in label for label in motor_labels)
    assert not any("5315" in label for label in motor_labels)
    assert not any("axisfly 3115" in label for label in motor_labels)

    # camera: universal — все камеры с остатком, в шапке universal_note
    cb_cam = FakeCb("b:slot:camera")
    await builder.cb_slot(cb_cam, state)
    cam_text, cam_kwargs = cb_cam.message.edits[-1]
    assert "подходит к любому дрону" in cam_text
    assert "найдено 4" in cam_text
    cam_labels = [
        label for label, cb in kb_data(cam_kwargs["reply_markup"])
        if cb.startswith("b:part:")
    ]
    assert any("тепловизионная" in label for label in cam_labels)
    assert any("дневная" in label for label in cam_labels)

    # frame: модель "size" — только рамы params.size == 10
    cb_frame = FakeCb("b:slot:frame")
    await builder.cb_slot(cb_frame, state)
    frame_text, frame_kwargs = cb_frame.message.edits[-1]
    assert "найдено 1" in frame_text
    frame_labels = [
        label for label, cb in kb_data(frame_kwargs["reply_markup"])
        if cb.startswith("b:part:")
    ]
    assert any("МАРК4" in label for label in frame_labels)
    assert not any("13" in label for label in frame_labels)
    # хотфикс Д2: рама без распознанного размера скрыта из подбора
    assert not any("Рама карбон" in label for label in frame_labels)


async def test_review_forced_motor_5315_on_13_shows_l1_error(tmp_path):
    """Форс-конфиг (черновик/правка): мотор 5315 на 13″ → в review ошибка L1
    «не применяется в серийных сборках 13″» (рядом — L3 по статору)."""
    rows = (*CSV_ROWS, "Мотор axisfly 5315 380KV", "Рама 13 дюймов")
    await _seed(tmp_path, rows)
    state = make_state()
    await state.update_data(
        mode="free", size=13,
        slots={
            "frame": {"key": part_key("Рама 13 дюймов"), "qty": 1},
            "motor": {"key": part_key("Мотор axisfly 5315 380KV"), "qty": 4},
        },
    )
    cb = FakeCb("b:review")
    await builder.cb_review(cb, state)
    text = cb.message.edits[-1][0]
    assert "не применяется в серийных сборках 13″" in text
    assert "статор 5315" in text                              # L3 тоже виден


async def test_review_explicit_size_drives_size_consistency(tmp_path):
    """Хотфиксы Д2+Д6: явный размер сборки 15″ (свободный режим) при раме
    Mark4 (распознана как 10″) → в итоге ошибка size_consistency «размер
    10″ не соответствует сборке 15″»: config итога включает явный size (Д6),
    и derive_build_size берёт его с приоритетом над размером рамы."""
    await _seed(tmp_path)
    state = make_state()
    msg = FakeMsg("/builder")
    await builder.cmd_builder(msg, state)
    await builder.cb_mode_free(FakeCb("b:mode:free"), state)
    await builder.cb_size(FakeCb("b:size:15"), state)
    # Этап 11в: 15″ — два числа моторов → экран b:motors:<n>; квадро = 4
    await builder.cb_motor_count(FakeCb("b:motors:4"), state)
    await fill_slot(state, FakeCb, "frame", part_key(FULL_CONFIG["frame"]))

    cb = FakeCb("b:review")
    await builder.cb_review(cb, state)
    text = cb.message.edits[-1][0]
    assert await state.get_state() == BuilderState.review
    assert "Рама 10 МАРК4-V2" in text
    assert "размер 10″ не соответствует сборке 15″" in text
    assert "❌" in text


# ============================================================
# 6) Этап 10в: рецепт-гексакоптер 15″ (6 моторов, два ESC)
# ============================================================

async def test_recipe_hexa_15_build_review_ok(tmp_path):
    """Hex-кнопка (гексакоптер): рецепт HEXA 4320 (15″, 6 моторов, два ESC) —
    раздельная пара fc+esc закрывает слот стека. Рамы в рецепте НЕТ (Этап 11в):
    карточка — «➖ Рама: под заказ (нет на складе)», слот frame — mto
    (key=None, made_to_order=True); упоминаний «Рама 15» нигде; review зелёный."""
    rows = (
        *CSV_ROWS,
        "Мотор axisfly 4320 380KV",
        "Пропеллеры HQ 15?7?3 (2CW+2CCW)",
        "Полетный контроллер Axisflying ECO F722",
        "Регулятор оборотов Axisflying ECO 8S 100A",
        "Видеопередатчик 4.9G/5.8G/6G 3 Вт + антенна",
        "Камера Axisflying Black Light",
        "Рама 15 дюймов",
    )
    await _seed(tmp_path, rows)
    state = make_state()
    recipe = next(
        r
        for r in await list_recipes(active_only=True)
        if r["title"] == "HEXA 4320 Hexacopter"
    )

    # серийный режим → Hex: только гекса-рецепт (не весь 15″-список)
    await builder.cb_mode_recipe(FakeCb("b:mode:recipe"), state)
    cb_15 = FakeCb("b:size:15")
    await builder.cb_size(cb_15, state)
    quad_titles = {t for t, _ in kb_data(cb_15.message.edits[-1][1]["reply_markup"])}
    assert "HEXA 4320 Hexacopter" not in " ".join(quad_titles)
    cb_hex = FakeCb("b:size:hex")
    await builder.cb_size(cb_hex, state)
    assert await state.get_state() == BuilderState.recipe_list
    assert (await state.get_data())["hexa"] is True
    hexa_titles = {t for t, _ in kb_data(cb_hex.message.edits[-1][1]["reply_markup"])}
    assert any("HEXA 4320" in t for t in hexa_titles)

    # карточка гекса: пара FC+ESC одной строкой стека; рама — «под заказ»
    # БЕЗ имени и БЕЗ дюймовой привязки (гард B4/D2)
    cb_card = FakeCb(f"b:recipe:{recipe['id']}")
    await builder.cb_recipe(cb_card, state)
    assert await state.get_state() == BuilderState.recipe_card
    card_text = cb_card.message.edits[-1][0]
    assert "Стек FC+ESC: Полетный контроллер Axisflying ECO F722" in card_text
    assert "Регулятор оборотов Axisflying ECO 8S" in card_text
    assert "Моторы: Мотор axisfly 4320 380KV · 6 шт" in card_text
    assert "➖ Рама: под заказ (нет на складе)" in card_text
    assert "Рама: любая" not in card_text
    assert "Рама HEX" not in card_text
    assert "рама 15" not in card_text.lower()

    # «Оформить сборку» → слоты рецепта; слот frame — mto «под заказ»
    cb_build = FakeCb(f"b:recipe:build:{recipe['id']}")
    await builder.cb_recipe_build(cb_build, state)
    assert await state.get_state() == BuilderState.slot_menu
    data = await state.get_data()
    assert data["mode"] == "recipe" and data["size"] == builder.HEXA_BUILD_SIZE
    assert data["motor_count"] == 6
    assert set(data["slots"]) == {"motor", "prop", "fc", "esc", "vtx", "camera", "rx", "frame"}
    assert data["slots"]["motor"]["qty"] == 6 and data["slots"]["esc"]["qty"] == 2
    assert data["slots"]["frame"] == {"key": None, "made_to_order": True}
    menu_text = cb_build.message.edits[-1][0]
    assert "➖ Рама: под заказ (нет на складе)" in menu_text
    assert "рама 15" not in menu_text.lower()
    assert (
        "✅ Стек FC+ESC: Полетный контроллер Axisflying ECO F722 "
        "+ Регулятор оборотов Axisflying ECO 8S 100A"
    ) in menu_text

    # review зелёный: расход L4 — мотор ×6 и ESC ×2 (остатки 50); рама —
    # mto-слот: в L1/L3/L4 не участвует, «деталь не найдена» нет.
    cb_review = FakeCb("b:review")
    await builder.cb_review(cb_review, state)
    review_text = cb_review.message.edits[-1][0]
    assert "✅ Всё совместимо" in review_text
    assert "❌" not in review_text
    assert "➖ Рама: под заказ (нет на складе)" in review_text
    assert "рама 15" not in review_text.lower()
    assert "Полётный контроллер: Полетный контроллер Axisflying ECO F722" in review_text
    assert "Регулятор оборотов (ESC): Регулятор оборотов Axisflying ECO 8S 100A" in review_text

    # черновик сохраняет раздельную пару fc/esc + mto-раму
    await builder.cb_save(FakeCb("b:save"), state)
    saved_slots = (await state.get_data())["slots"]
    draft = (await list_drafts(42))[0]
    assert set(draft["config"]["slots"]) == set(saved_slots)
    assert saved_slots["motor"]["qty"] == 6 and saved_slots["esc"]["qty"] == 2
    assert saved_slots["frame"] == {"key": None, "made_to_order": True}



# ============================================================
# 7) Этап 11в: свободный режим 6 моторов — рама «под заказ», whitelist (15, 4/6)
# ============================================================

async def test_free_mode_6_motors_frame_mto_and_whitelist(tmp_path):
    """Свободный режим 15″: два числа моторов → экран b:motors:<n>;
    6 моторов: слот frame — экран «под заказ» (складских рам в списке нет),
    mto → review ok=True; 4 мотора: в списке моторов НЕТ гекса-мотора 4320."""
    rows = (
        *CSV_ROWS,
        "Мотор axisfly 4320 380KV",
        "Пропеллеры HQ 15?7?3 (2CW+2CCW)",
        "Полетный контроллер Axisflying ECO F722",
        "Регулятор оборотов Axisflying ECO 8S 100A",
        "Рама 15 дюймов",
    )
    await _seed(tmp_path, rows)

    # 15″: у серийных сборок два числа моторов (4 и 6) → экран выбора
    state = make_state()
    await builder.cb_mode_free(FakeCb("b:mode:free"), state)
    cb_size = FakeCb("b:size:15")
    await builder.cb_size(cb_size, state)
    assert await state.get_state() == BuilderState.choosing_size
    text, kwargs = cb_size.message.edits[-1]
    assert "количество моторов" in text
    cbs = {cb for _, cb in kb_data(kwargs["reply_markup"])}
    assert "b:motors:4" in cbs and "b:motors:6" in cbs

    # 6 моторов: слот frame — экран mto, ни одной складской рамы в списке
    await builder.cb_motor_count(FakeCb("b:motors:6"), state)
    assert await state.get_state() == BuilderState.slot_menu
    assert (await state.get_data())["motor_count"] == 6
    cb_frame = FakeCb("b:slot:frame")
    await builder.cb_slot(cb_frame, state)
    frame_text, frame_kwargs = cb_frame.message.edits[-1]
    assert "Рамы для конфигураций на 6/8 моторов на складе нет" in frame_text
    assert "➖ Оформить раму под заказ" in frame_text
    frame_cbs = [cb for _, cb in kb_data(frame_kwargs["reply_markup"])]
    assert "b:mto_frame" in frame_cbs
    assert not any(cb.startswith("b:part:") for cb in frame_cbs)

    # «Рама под заказ» → mto-слот; заполняем гекса-набор → review ok=True
    await builder.cb_mto_frame(FakeCb("b:mto_frame"), state)
    assert await state.get_state() == BuilderState.slot_menu
    assert (await state.get_data())["slots"]["frame"] == {"key": None, "made_to_order": True}
    for category, name in {
        "motor": "Мотор axisfly 4320 380KV",
        "prop": "Пропеллеры HQ 15?7?3 (2CW+2CCW)",
        "fc": "Полетный контроллер Axisflying ECO F722",
        "esc": "Регулятор оборотов Axisflying ECO 8S 100A",
        "vtx": "Видеопередатчик Axisflying 3W 5.8ГГц",
        "camera": "Камера FPV 1800 TVL",
        "rx": "Приемник (RX) 915MHz",
    }.items():
        await fill_slot(state, FakeCb, category, part_key(name))
    cb_review = FakeCb("b:review")
    await builder.cb_review(cb_review, state)
    review_text = cb_review.message.edits[-1][0]
    assert "✅ Всё совместимо" in review_text
    assert "❌" not in review_text
    assert "➖ Рама: под заказ (нет на складе)" in review_text
    assert "рама 15" not in review_text.lower()

    # 4 мотора: L1-union(15, 4) — гекса-мотор 4320 в списке моторов отсутствует
    state4 = make_state()
    await builder.cb_mode_free(FakeCb("b:mode:free"), state4)
    await builder.cb_size(FakeCb("b:size:15"), state4)
    await builder.cb_motor_count(FakeCb("b:motors:4"), state4)
    assert (await state4.get_data())["motor_count"] == 4
    cb_motor = FakeCb("b:slot:motor")
    await builder.cb_slot(cb_motor, state4)
    _motor_text, motor_kwargs = cb_motor.message.edits[-1]
    motor_cbs = [cb for _, cb in kb_data(motor_kwargs["reply_markup"]) if cb.startswith("b:part:")]
    m4320 = await get_part(part_key("Мотор axisfly 4320 380KV"))
    assert m4320 is not None
    assert f"b:part:{m4320['id']}" not in motor_cbs

    # …а при 6 моторах он в списке ЕСТЬ (union(15, 6) содержит 4320)
    state6 = make_state()
    await builder.cb_mode_free(FakeCb("b:mode:free"), state6)
    await builder.cb_size(FakeCb("b:size:15"), state6)
    await builder.cb_motor_count(FakeCb("b:motors:6"), state6)
    cb_motor6 = FakeCb("b:slot:motor")
    await builder.cb_slot(cb_motor6, state6)
    motor6_cbs = [
        cb
        for _, cb in kb_data(cb_motor6.message.edits[-1][1]["reply_markup"])
        if cb.startswith("b:part:")
    ]
    assert f"b:part:{m4320['id']}" in motor6_cbs

