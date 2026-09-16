"""Интеграционные проверки: сборка bot.py, порядок роутеров, скрипты _verify_*."""

import asyncio

EXPECTED_ROUTER_NAMES = [
    "calculator", "menu", "scenarios", "followup", "faq", "objections",
    "selection", "search", "build", "voice",
]


def test_bot_assembles_with_router_order():
    """Калькулятор — первым (FSM не должен перехватываться fallback'ом AI),
    fallback — последним. У selection/build/voice имена автогенерируются —
    проверяем их по объектам роутеров."""
    import bot
    from handlers import build, search, selection, voice
    from handlers.fallback import router as fallback_router

    subs = bot.dp.sub_routers
    names = [getattr(r, "name", None) for r in subs]
    assert names[:6] == EXPECTED_ROUTER_NAMES[:6], names
    assert subs[6] is selection.router
    assert subs[7] is search.router
    assert subs[8] is build.router
    assert subs[9] is voice.router
    assert subs[-1] is fallback_router, "fallback обязан быть последним"
    assert all(r is not fallback_router for r in subs[:-1])


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