"""Интеграционные проверки: сборка bot.py, порядок роутеров, скрипты _verify_*."""

import asyncio

EXPECTED_ROUTER_NAMES = [
    "admin", "early_buttons", "calculator", "menu", "cheatsheets", "scenarios",
    "followup", "faq", "objections",
]


def test_bot_assembles_with_router_order():
    """Админ-роутер (/stats, /add_faq, /update_price) — раньше всех: админ-команды
    и шаги админ-FSM не должны перехватываться FSM-сценариями (калькулятор ловит
    любой текст в своих состояниях) и fallback'ом AI. «Ранние» кнопки меню
    (early_buttons: «🤖 AI-ассистент», «📚 Шпаргалки») — раньше всех FSM-сценариев:
    кнопки обязаны открывать свои разделы из любого сценария, а не съедаться как
    «выход в меню». Калькулятор — сразу после (FSM не должен перехватываться
    fallback'ом AI), «📚 Шпаргалки» — сразу после menu, fallback — последним.
    У selection/search/build/voice имена автогенерируются — проверяем их по
    объектам роутеров. ЭТАП 7: «builder» добавлен в контракт на фактическую
    позицию регистрации в bot.py — сразу после build и до voice (FSM-ввод
    part_input конструктора не должен уходить в fallback AI); имя у него явное
    (Router(name="builder") в handlers/builder.py), поэтому проверяем и по имени."""
    import bot
    from handlers import build, search, selection, voice
    from handlers.admin import router as admin_router
    from handlers.builder import router as builder_router
    from handlers.cheatsheets import router as cheatsheets_router
    from handlers.early_buttons import router as early_buttons_router
    from handlers.fallback import router as fallback_router

    subs = bot.dp.sub_routers
    names = [getattr(r, "name", None) for r in subs]
    assert names[:9] == EXPECTED_ROUTER_NAMES, names
    assert subs[0] is admin_router, "админ-команды обязаны стоять раньше FSM-сценариев и fallback"
    assert subs[1] is early_buttons_router, "ранние кнопки меню обязаны стоять раньше FSM-сценариев"
    assert subs[4] is cheatsheets_router, "cheatsheets обязан стоять сразу после menu"
    assert subs[9] is selection.router
    assert subs[10] is search.router
    assert subs[11] is build.router
    assert names[12] == "builder", names
    assert subs[12] is builder_router
    assert subs[13] is voice.router
    assert subs[-1] is fallback_router, "fallback обязан быть последним"
    assert all(r is not fallback_router for r in subs[:-1])


def test_early_buttons_registered_before_fsm_scenarios():
    """«🤖 AI-ассистент» и «📚 Шпаргалки» в early_buttons указывают на единственные
    реализации (button_ai_assistant / button_cheatsheets с state.clear() внутри) —
    поэтому нажатие во время любого FSM-сценария завершает сценарий и сразу
    открывает раздел, а не съедается как «выход в меню»."""
    from handlers import cheatsheets, early_buttons, menu

    registered = [h.callback for h in early_buttons.router.message.handlers]
    assert menu.button_ai_assistant in registered, registered
    assert cheatsheets.button_cheatsheets in registered, registered


def test_verify_calc_script():
    """_verify_calc.py выполняет проверки при импорте (asyncio.run в хвосте модуля);
    упавшие assert'ы должны уронить и этот тест."""
    import _verify_calc  # noqa: F401


def test_verify_followup_script():
    """«⏰ Напоминания» + ЭТАП 2 (утренняя сводка о follow-up) — проверки при импорте."""
    import _verify_followup  # noqa: F401


def test_verify_scenarios_script():
    import _verify_scenarios
    asyncio.run(_verify_scenarios.main())


def test_verify_ai_providers_script():
    import _verify_ai_providers
    asyncio.run(_verify_ai_providers._run_tests())


def test_verify_builder_script():
    """«🛠 Конструктор» (Этапы 1–7): matcher/classifier/compat/keyboards/фасады —
    чек-поинты _verify_builder.py; main() возвращает 0 (иначе тест красный)."""
    import _verify_builder

    assert asyncio.run(_verify_builder.main()) == 0