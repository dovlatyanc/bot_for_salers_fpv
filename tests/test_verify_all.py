"""Интеграционные проверки: сборка bot.py, порядок роутеров, скрипты _verify_*."""

import asyncio

EXPECTED_ROUTER_NAMES = [
    "early_buttons", "calculator", "menu", "cheatsheets", "scenarios", "followup", "faq",
    "objections",
]


def test_bot_assembles_with_router_order():
    """«Ранние» кнопки меню (early_buttons: «🤖 AI-ассистент», «📚 Шпаргалки») —
    раньше всех FSM-сценариев: кнопки обязаны открывать свои разделы из любого
    сценария, а не съедаться как «выход в меню». Калькулятор — сразу после
    (FSM не должен перехватываться fallback'ом AI), «📚 Шпаргалки» — сразу после
    menu, fallback — последним. У selection/build/voice имена автогенерируются —
    проверяем их по объектам роутеров."""
    import bot
    from handlers import build, search, selection, voice
    from handlers.cheatsheets import router as cheatsheets_router
    from handlers.early_buttons import router as early_buttons_router
    from handlers.fallback import router as fallback_router

    subs = bot.dp.sub_routers
    names = [getattr(r, "name", None) for r in subs]
    assert names[:8] == EXPECTED_ROUTER_NAMES, names
    assert subs[0] is early_buttons_router, "ранние кнопки меню обязаны стоять раньше FSM-сценариев"
    assert subs[3] is cheatsheets_router, "cheatsheets обязан стоять сразу после menu"
    assert subs[8] is selection.router
    assert subs[9] is search.router
    assert subs[10] is build.router
    assert subs[11] is voice.router
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