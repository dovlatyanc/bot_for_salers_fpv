"""Хендлеры «Конструктора» (Этап 6): smoke-проверки.

Стиль tests/test_handlers_selection.py: экспорт из проекта цел, BuilderState
в бот-роутерах до fallback, /builder отвечает стартовым экраном. Покрытие
callback-префиксов — через h.check(SimpleNamespace(data=...)) на handlers
роутера (полный E2E-прогон через Telegram-диспетчер — Этап 7). Флоу-смоук —
прямые вызовы хендлеров с реальной БД (fixture tmp_db, см. conftest).
"""

from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

import bot
from db import get_sessionmaker
from db.stock_import import import_stock_csv
from db.repositories import get_part, list_drafts, list_recipes, save_draft, seed_recipes
from handlers import builder
from handlers.builder import CALLBACK_PREFIXES, router
from handlers.build import router as build_router
from handlers.fallback import router as fallback_router
from keyboards.builder import (
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
)
from services.builder.labels import label
from states.states import BuilderState
from tests.fakes import FakeCb, FakeMsg, kb_data, make_state

CSV_TEXT = (
    "Рама 10 МАРК4-V2, ;шт;2,000\r\n"
    "Рама 15 дюймов, ;шт;1,000\r\n"
    "Рама карбон, ;шт;1,000\r\n"           # размер рамы не распознан (хотфикс Д2)
    "Мотор axisfly 3115 900KV, ;шт;4,000\r\n"
    "Приемник (RX) 915MHz, ;шт;3,000\r\n"
    "Приемник (RX) 868MHz, ;шт;2,000\r\n"
)


async def _seed_csv(tmp_path) -> None:
    """Импорт CSV + перевысид рецептов (прод-поток: остатки → дозаполнение part_key)."""
    path = tmp_path / "Остатки.csv"
    path.write_text(CSV_TEXT, encoding="cp1251")
    await import_stock_csv(path)
    async with get_sessionmaker()() as session:
        await seed_recipes(session)


# ============================================================
# 1) экспорт из проекта и регистрация в боте
# ============================================================

def test_builder_router_registered_between_build_and_fallback():
    routers = bot.dp.sub_routers
    assert router in routers
    idx = routers.index(router)
    assert routers[idx - 1] is build_router          # сразу после «Своя сборка»
    assert routers.index(fallback_router) > idx      # и до fallback (AI не перехватит ввод)


def test_builder_callback_prefixes_fixed():
    assert CALLBACK_PREFIXES == frozenset(
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


# ============================================================
# 2) BuilderState: 9 состояний и реэкспорт
# ============================================================

def test_builder_state_nine_states():
    assert [s.state for s in BuilderState.__states__] == [
        "BuilderState:start",
        "BuilderState:choosing_size",
        "BuilderState:recipe_list",
        "BuilderState:recipe_card",
        "BuilderState:slot_menu",
        "BuilderState:part_list",
        "BuilderState:part_input",
        "BuilderState:review",
        "BuilderState:drafts",
    ]


def test_builder_state_reexport_matches_states():
    from states import BuilderState as BuilderStateFromPackage

    assert BuilderState is BuilderStateFromPackage


# ============================================================
# 3) покрытие callback-префиксов клавиатур хендлерами роутера
# ============================================================

def _all_keyboard_callbacks() -> set[str]:
    """Совокупность callback_data всех клавиатур keyboards/builder.py."""
    cbs: set[str] = set()
    cbs.update(cb for _, cb in kb_data(kb_start()))
    cbs.update(cb for _, cb in kb_data(kb_size_menu([10, 13, 15, "hex"])))
    cbs.update(cb for _, cb in kb_data(kb_recipe_list([{"id": 1, "title": "А", "builds": 2}])))
    cbs.update(cb for _, cb in kb_data(kb_recipe(1)))
    cbs.update(cb for _, cb in kb_data(kb_slot_menu({}, None)))
    cbs.update(cb for _, cb in kb_data(kb_slot_menu({"frame": {"key": "k", "qty": 1}}, 10)))
    cbs.update(
        cb
        for _, cb in kb_data(kb_slot_menu({"frame": {"key": None, "made_to_order": True}}, 15))
    )
    cbs.update(cb for _, cb in kb_data(kb_motor_count([6, 4])))
    cbs.update(cb for _, cb in kb_data(kb_frame_mto()))
    parts = [{"id": i, "key": f"k{i}", "name": f"Деталь {i}", "quantity": 1.0} for i in range(11)]
    cbs.update(cb for _, cb in kb_data(kb_part_list(parts, 0, 2, "frame")))
    cbs.update(cb for _, cb in kb_data(kb_part_list(parts[10:], 1, 2, "frame")))
    cbs.update(
        cb
        for _, cb in kb_data(
            kb_review({"ok": False, "missing": ["vtx"], "errors": [], "warnings": []})
        )
    )
    cbs.update(cb for _, cb in kb_data(kb_drafts([{"id": 1, "name": "А"}, {"id": 2, "name": "Б"}])))
    return cbs


async def test_every_keyboard_callback_has_handler():
    for callback_data in sorted(_all_keyboard_callbacks()):
        if callback_data == "dummy":
            continue  # «псевдо»-кнопки (индикатор страницы) — глобальный no-op menu.py
        matched = False
        for handler in router.callback_query.handlers:
            ok, _kwargs = await handler.check(SimpleNamespace(data=callback_data))
            if ok:
                matched = True
                break
        assert matched, f"нет хендлера для callback {callback_data!r}"


# ============================================================
# 4) /builder
# ============================================================

async def test_cmd_builder_opens_start():
    message = FakeMsg("/builder")
    state = make_state()
    await builder.cmd_builder(message, state)
    assert await state.get_state() == BuilderState.start
    text, kwargs = message.answers[-1]
    assert "Конструктор дронов" in text
    assert [cb for _, cb in kb_data(kwargs["reply_markup"])] == [
        "b:mode:recipe",
        "b:mode:free",
        "b:start:resume",
        "b:start:drafts",
    ]
# ============================================================
# 5) флоу-смоук: прямые вызовы хендлеров с реальной БД
# ============================================================

async def test_new_flow_slot_and_part(tmp_path):
    """b:mode:free → b:size:10 → slot_menu → b:slot:rx → part_list → b:part:<id>.

    Свободный режим, модель совместимости (Этап 10): rx — категория "freq" —
    L1-whitelist её не режет, до выбора TX видны оба приёмника (915 и 868);
    пересечение частот после выбора передатчика отфильтрует matcher.
    """
    await _seed_csv(tmp_path)
    state = make_state()
    await builder.cb_mode_free(FakeCb("b:mode:free"), state)
    assert await state.get_state() == BuilderState.choosing_size
    assert (await state.get_data())["mode"] == "free"

    await builder.cb_size(FakeCb("b:size:10"), state)
    assert await state.get_state() == BuilderState.slot_menu
    assert (await state.get_data())["slots"] == {}
    assert (await state.get_data())["size"] == 10

    cb_slot = FakeCb("b:slot:rx")
    await builder.cb_slot(cb_slot, state)
    assert await state.get_state() == BuilderState.part_list
    assert (await state.get_data())["current_slot"] == "rx"
    text = cb_slot.message.edits[-1][0]
    assert label("rx") in text and "найдено 2" in text  # freq: L1 не режет

    rx = await get_part("приемник (rx) 915mhz")  # id для callback_data (Этап 9а)
    cb_part = FakeCb(f"b:part:{rx['id']}")
    await builder.cb_part(cb_part, state)
    assert (await state.get_data())["slots"] == {"rx": {"key": "приемник (rx) 915mhz", "qty": 1}}
    assert await state.get_state() == BuilderState.slot_menu
    assert "✅ Приёмник управления (RX): Приемник (RX) 915MHz · 3 шт" in cb_part.message.edits[-1][0]


async def test_cb_size_old_query_does_not_crash(tmp_path):
    """Этап 11д: боевой краш «query is too old» — просроченный ack НЕ роняет
    cb_size (ранний safe_answer) и следующий экран рендерится."""
    await _seed_csv(tmp_path)
    state = make_state()
    await builder.cb_mode_free(FakeCb("b:mode:free"), state)

    old = (
        "Bad Request: query is too old and response timeout expired "
        "or query ID is invalid"
    )

    class OldQueryCb(FakeCb):
        async def answer(self, text=None, show_alert=False):
            raise TelegramBadRequest(method=None, message=old)

    cb = OldQueryCb("b:size:10")
    await builder.cb_size(cb, state)  # без исключения
    assert await state.get_state() == BuilderState.slot_menu
    assert cb.message.edits          # следующий экран отрендерен


async def test_free_mode_empty_union_shows_hint(tmp_path):
    """Свободный режим 13″: стек — "recipe"-слот, но в серийных 13″ сборках
    стеков нет (только раздельные FC+ESC) → union пуст → «этот узел
    собирается иначе» + кнопка перехода в серийный режим."""
    await _seed_csv(tmp_path)
    state = make_state()
    await builder.cb_mode_free(FakeCb("b:mode:free"), state)
    await builder.cb_size(FakeCb("b:size:13"), state)
    cb_slot = FakeCb("b:slot:fc_esc_stack")
    await builder.cb_slot(cb_slot, state)
    text, kwargs = cb_slot.message.edits[-1]
    assert "собирается иначе" in text
    assert "Серийные конфигурации" in text
    assert ("📦 Серийные конфигурации", "b:mode:recipe") in kb_data(kwargs["reply_markup"])
    assert await state.get_state() == BuilderState.part_list


async def test_recipe_flow_list_card_use(tmp_path):
    """Серийный режим: b:mode:recipe → b:size:10 → список рецептов → карточка → в сборку."""
    await _seed_csv(tmp_path)
    state = make_state()
    await builder.cb_mode_recipe(FakeCb("b:mode:recipe"), state)
    assert await state.get_state() == BuilderState.choosing_size
    assert (await state.get_data())["mode"] == "recipe"

    await builder.cb_size(FakeCb("b:size:10"), state)
    assert await state.get_state() == BuilderState.recipe_list
    recipes = await list_recipes(size=10, active_only=True)
    assert len(recipes) == 5  # 10″-рецепты сидируются init_db даже в tmp-БД

    recipe = recipes[0]
    cb_card = FakeCb(f"b:recipe:{recipe['id']}")
    await builder.cb_recipe(cb_card, state)
    assert await state.get_state() == BuilderState.recipe_card
    card_text = cb_card.message.edits[-1][0]
    assert recipe["title"] in card_text
    assert label("motor") in card_text  # мотор есть во всех рецептах; рам — нет
    assert "Рама: любая 10″ — выбирается на шаге оформления" in card_text

    cb_use = FakeCb(f"b:recipe:build:{recipe['id']}")
    await builder.cb_recipe_build(cb_use, state)
    assert await state.get_state() == BuilderState.slot_menu
    slots = (await state.get_data())["slots"]
    assert slots == {
        s["category"]: {"key": s["part_key"], "qty": int(s.get("qty") or 1)}
        for s in recipe["slots"] or []
        if s.get("part_key")
    }
    assert set(slots) == {"rx"}  # в tmp-БД из CSV резолвится только приёмник 915MHz


async def test_hex_flow_recipe_list_card_build(tmp_path):
    """Hex: b:mode:recipe → b:size:hex → только гекса-рецепты; рамы в рецепте
    НЕТ (Этап 11в): карточка печатает «➖ Рама: под заказ (нет на складе)»,
    слот frame в сборке — mto (key=None, made_to_order=True); упоминаний
    «Рама 15» в гекса-контексте нет нигде."""
    await _seed_csv(tmp_path)
    state = make_state()
    await builder.cb_mode_recipe(FakeCb("b:mode:recipe"), state)
    await builder.cb_size(FakeCb("b:size:hex"), state)
    assert await state.get_state() == BuilderState.recipe_list
    assert (await state.get_data())["hexa"] is True
    assert (await state.get_data())["size"] == builder.HEXA_BUILD_SIZE
    hexa_recipes = [
        r for r in await list_recipes(active_only=True) if builder._is_hexa_recipe(r)
    ]
    assert hexa_recipes  # гекса-рецепт сидируется init_db
    recipe = hexa_recipes[0]

    cb_card = FakeCb(f"b:recipe:{recipe['id']}")
    await builder.cb_recipe(cb_card, state)
    card_text = cb_card.message.edits[-1][0]
    assert "➖ Рама: под заказ (нет на складе)" in card_text
    assert "Рама: любая" not in card_text
    lowered = card_text.lower()
    assert "рама 15" not in lowered and "15″ рам" not in lowered
    assert "Рама HEX" not in card_text  # имени рамы-детали в карточке нет

    cb_use = FakeCb(f"b:recipe:build:{recipe['id']}")
    await builder.cb_recipe_build(cb_use, state)
    data = await state.get_data()
    slots = data["slots"]
    assert slots["frame"] == {"key": None, "made_to_order": True}
    assert data["motor_count"] == 6  # qty слота motor гекса-рецепта
    menu_text = cb_use.message.edits[-1][0]
    assert "➖ Рама: под заказ (нет на складе)" in menu_text
    assert "рама 15" not in menu_text.lower()


async def test_part_stale_callback_alerts_and_rerenders(tmp_path):
    """Старая закэшированная кнопка b:part:<ключ-имя> (до Этапа 9а) → alert + перерендер.

    Ключи-имена из старых сообщений чата в новую схему не парсятся: cb_part
    отвечает «Список устарел, обновляю…» (show_alert) и перерисовывает
    part_list; слот остаётся незаполненным. Тот же путь — несуществующий id.
    """
    await _seed_csv(tmp_path)
    state = make_state()
    await builder.cb_mode_free(FakeCb("b:mode:free"), state)
    await builder.cb_size(FakeCb("b:size:10"), state)
    await builder.cb_slot(FakeCb("b:slot:frame"), state)

    stale_key = "полетный контроллер + регулятор оборотов geprc taker f722 bls 80a v2"
    cb = FakeCb(f"b:part:{stale_key}")
    await builder.cb_part(cb, state)
    assert any("Список устарел" in (text or "") for text, _alert in cb.alerts)
    assert any(alert is True for _text, alert in cb.alerts)          # show_alert=True
    assert await state.get_state() == BuilderState.part_list         # список перерисован
    assert (await state.get_data())["slots"] == {}                   # слот не заполнен

    cb2 = FakeCb("b:part:99999")                                     # несуществующий id
    await builder.cb_part(cb2, state)
    assert any("Список устарел" in (text or "") for text, _alert in cb2.alerts)


async def test_motor_candidates_respect_build_size(tmp_path):
    """Рама 10 выбрана → в слоте motor только совместимый мотор (MOTOR_STATOR_BY_SIZE)."""
    await _seed_csv(tmp_path)
    state = make_state()
    await state.update_data(
        slots={"frame": {"key": "рама 10 марк4-v2", "qty": 1}}, current_slot="motor"
    )
    cb_slot = FakeCb("b:slot:motor")
    await builder.cb_slot(cb_slot, state)
    text = cb_slot.message.edits[-1][0]
    assert "найдено 1" in text
    motor = await get_part("мотор axisfly 3115 900kv")
    rows = kb_data(cb_slot.message.edits[-1][1]["reply_markup"])
    assert ("Мотор axisfly 3115 900KV · 4 шт", f"b:part:{motor['id']}") in rows


async def test_free_mode_frame_filter_hides_sizeless(tmp_path):
    """Хотфикс Д2 (свободный режим): в слоте frame при известном размере
    сборки видны только рамы с совпадающим params["size"]; рамы без
    распознанного размера скрыты (раньше оставались в списке)."""
    await _seed_csv(tmp_path)
    state = make_state()
    await builder.cb_mode_free(FakeCb("b:mode:free"), state)
    await builder.cb_size(FakeCb("b:size:10"), state)
    cb_slot = FakeCb("b:slot:frame")
    await builder.cb_slot(cb_slot, state)
    text = cb_slot.message.edits[-1][0]
    assert "найдено 1" in text                     # только МАРК4 (10″)
    rows = kb_data(cb_slot.message.edits[-1][1]["reply_markup"])
    labels = [lbl for lbl, _cb in rows if _cb.startswith("b:part:")]
    assert any("МАРК4" in lbl for lbl in labels)
    assert not any("Рама карбон" in lbl for lbl in labels)   # без размера — скрыта
    assert not any("15 дюймов" in lbl for lbl in labels)


async def test_part_input_filters(tmp_path):
    """Текст «10 дюймов» в part_input → фильтр → только рама 10."""
    await _seed_csv(tmp_path)
    state = make_state()
    await state.update_data(current_slot="frame")
    await builder.cb_input(FakeCb("b:input"), state)
    assert await state.get_state() == BuilderState.part_input

    message = FakeMsg("10 дюймов")
    await builder.process_part_input(message, state)
    assert await state.get_state() == BuilderState.part_list
    assert (await state.get_data())["filters"] == {"size": 10}
    text = message.answers[-1][0]
    assert "Фильтр: 10″" in text and "найдено 1" in text


async def test_part_list_pagination_clamped(tmp_path):
    """b:page:+1/-1: страница зажимается границами (2 кандидата → всегда 1/1)."""
    await _seed_csv(tmp_path)
    state = make_state()
    await state.update_data(current_slot="frame", page=5)
    cb_plus = FakeCb("b:page:+1")
    await builder.cb_page(cb_plus, state)
    assert "стр. 1 из 1" in cb_plus.message.edits[-1][0]  # страница 5 → зажата в 0
    cb_minus = FakeCb("b:page:-1")
    await builder.cb_page(cb_minus, state)
    assert "стр. 1 из 1" in cb_minus.message.edits[-1][0]
    assert await state.get_state() == BuilderState.part_list


async def test_review_renders_incomplete(tmp_path):
    """b:review с одной рамой → «Некомплектно» (compat, Этап 5) + кнопки итога."""
    await _seed_csv(tmp_path)
    state = make_state()
    await state.update_data(slots={"frame": {"key": "рама 10 марк4-v2", "qty": 1}})
    cb = FakeCb("b:review")
    await builder.cb_review(cb, state)
    assert await state.get_state() == BuilderState.review
    text = cb.message.edits[-1][0]
    assert "📋 Проверка сборки" in text
    assert "10″" in text
    assert "Не выбрано" in text
    assert [cb_ for _, cb_ in kb_data(cb.message.edits[-1][1]["reply_markup"])] == [
        "b:back",
        "b:save",
        "b:done",
    ]


async def test_save_updates_same_draft_then_done(tmp_path):
    """b:save → черновик в БД; повторный save обновляет его; b:done завершает FSM."""
    await _seed_csv(tmp_path)
    state = make_state()
    await state.update_data(slots={"frame": {"key": "рама 10 марк4-v2", "qty": 1}})

    await builder.cb_save(FakeCb("b:save"), state)
    drafts = await list_drafts(42)
    assert len(drafts) == 1
    assert drafts[0]["config"]["slots"]["frame"]["key"] == "рама 10 марк4-v2"
    assert drafts[0]["name"].startswith("Черновик ")
    assert (await state.get_data())["draft_id"] == drafts[0]["id"]

    await state.update_data(slots={"frame": {"key": "рама 15 дюймов", "qty": 1}})
    await builder.cb_save(FakeCb("b:save"), state)
    drafts = await list_drafts(42)
    assert len(drafts) == 1  # draft_id из state → update, а не новая запись
    assert drafts[0]["config"]["slots"]["frame"]["key"] == "рама 15 дюймов"

    cb_done = FakeCb("b:done")
    await builder.cb_done(cb_done, state)
    assert await state.get_state() is None
    assert "Черновик сохранён" in cb_done.message.answers[-1][0]


async def test_drafts_list_load_delete(tmp_path):
    """b:start:drafts → b:draft:load:<id> → slot_menu; b:draft:del:<id> → пусто."""
    await _seed_csv(tmp_path)
    draft = await save_draft(
        42, {"slots": {"frame": {"key": "рама 10 марк4-v2", "qty": 1}}}, name="Мой борт"
    )

    state = make_state()
    cb_list = FakeCb("b:start:drafts")
    await builder.cb_start_drafts(cb_list, state)
    assert await state.get_state() == BuilderState.drafts
    assert "Мой борт" in cb_list.message.edits[-1][0]

    cb_load = FakeCb(f"b:draft:load:{draft['id']}")
    await builder.cb_draft_load(cb_load, state)
    assert await state.get_state() == BuilderState.slot_menu
    assert (await state.get_data())["slots"] == {"frame": {"key": "рама 10 марк4-v2", "qty": 1}}
    assert (await state.get_data())["draft_id"] == draft["id"]

    cb_del = FakeCb(f"b:draft:del:{draft['id']}")
    await builder.cb_draft_del(cb_del, state)
    assert await list_drafts(42) == []
    assert await state.get_state() == BuilderState.drafts  # остаёмся в списке


async def test_resume_loads_latest_draft():
    """b:start:resume → свежий черновик (list_drafts, свежие первыми) → slot_menu."""
    await save_draft(42, {"slots": {"frame": {"key": "рама 15 дюймов", "qty": 1}}}, name="Старый")
    await save_draft(42, {"slots": {"vtx": {"key": "dmkr 7.2g 5w vtx", "qty": 1}}}, name="Новый")

    state = make_state()
    await builder.cb_start_resume(FakeCb("b:start:resume"), state)
    assert await state.get_state() == BuilderState.slot_menu
    data = await state.get_data()
    assert data["slots"] == {"vtx": {"key": "dmkr 7.2g 5w vtx", "qty": 1}}
    assert data["draft_id"] is not None


async def test_resume_without_drafts_alerts_and_stays():
    state = make_state()
    cb = FakeCb("b:start:resume")
    await builder.cb_start_resume(cb, state)
    assert await state.get_state() is None  # стартовый экран не открывался
    assert any("Черновиков пока нет" in (text or "") for text, _alert in cb.alerts)


async def test_back_from_drafts_to_start_and_from_part_list():
    state = make_state()
    await state.set_state(BuilderState.drafts)
    cb1 = FakeCb("b:back")
    await builder.cb_back(cb1, state)
    assert await state.get_state() == BuilderState.start

    state = make_state()
    await state.update_data(current_slot="frame")
    await state.set_state(BuilderState.part_list)
    cb2 = FakeCb("b:back")
    await builder.cb_back(cb2, state)
    assert await state.get_state() == BuilderState.slot_menu


async def test_cancel_resets_to_start():
    state = make_state()
    await state.update_data(slots={"frame": {"key": "k", "qty": 1}})
    await state.set_state(BuilderState.slot_menu)
    cb = FakeCb("b:cancel")
    await builder.cb_cancel(cb, state)
    assert await state.get_state() == BuilderState.start
    assert (await state.get_data()) == {
        "slots": {},
        "draft_id": None,
        "current_slot": None,
        "page": 0,
        "filters": {},
    }


async def test_part_input_not_text_hint():
    state = make_state()
    await state.set_state(BuilderState.part_input)
    message = FakeMsg(None)  # не-текст (text отсутствует)
    await builder.process_part_input_not_text(message)
    assert message.answers and "Отправьте запрос текстом" in message.answers[-1][0]
    assert await state.get_state() == BuilderState.part_input  # состояние не сброшено

