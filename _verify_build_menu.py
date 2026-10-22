"""Временная E2E-проверка фиксов: выход из FSM по кнопкам меню («Своя сборка»,
«Обратная связь») и новый хендлер кнопки «🤖 AI-ассистент». Удаляется после проверки."""
import asyncio
from types import SimpleNamespace

import handlers.build as build  # noqa: E402
import handlers.menu as menu  # noqa: E402
from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from states import BuildState, FeedbackState  # noqa: E402


class Msg:
    def __init__(self, text=None):
        self.text = text
        self.sent = []
        # from_user нужен хендлерам для диалогового логирования
        self.from_user = SimpleNamespace(id=42, username="tester")

    async def answer(self, text, **kwargs):
        self.sent.append(text)


async def main():
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)

    async def new_ctx():
        ctx = FSMContext(storage=storage, key=key)
        await ctx.clear()
        return ctx

    # 1) «Своя сборка» → ввод конфигурации → нажатие «🤖 AI-ассистент»
    ctx = await new_ctx()
    await ctx.set_state(BuildState.waiting_for_config)
    msg = Msg("🤖 AI-ассистент")
    await build.process_build(msg, ctx)
    assert await ctx.get_state() is None, "FSM должен быть очищен"
    assert any("Своя сборка закрыта" in t for t in msg.sent), msg.sent
    print("[OK] «AI-ассистент» во время ввода конфигурации -> чистый выход из FSM")

    # 2) другие кнопки меню и «отмена» во время ввода конфигурации
    for text in ("📚 Шпаргалки", "📊 ТТХ", "🔎 Поиск", "💰 Калькулятор", "отмена"):
        ctx = await new_ctx()
        await ctx.set_state(BuildState.waiting_for_config)
        msg = Msg(text)
        await build.process_build(msg, ctx)
        assert await ctx.get_state() is None, text
        assert any("Своя сборка закрыта" in t for t in msg.sent), (text, msg.sent)
    print("[OK] кнопки меню и «отмена» во время ввода конфигурации -> чистый выход")

    # 3) нормальный ввод конфигурации не сломан
    ctx = await new_ctx()
    await ctx.set_state(BuildState.waiting_for_config)
    msg = Msg("14S12P")
    await build.process_build(msg, ctx)
    assert await ctx.get_state() == BuildState.waiting_for_connector, await ctx.get_state()
    print("[OK] «14S12P» -> шаг выбора разъёма (обычный сценарий не сломан)")

    # 4) на шаге разъёма кнопка меню — выход, а не «тип разъёма»
    await ctx.update_data(s=14, p=12)
    msg = Msg("🤖 AI-ассистент")
    await build.process_build_connector(msg, ctx)
    assert await ctx.get_state() is None
    assert any("Своя сборка закрыта" in t for t in msg.sent), msg.sent
    print("[OK] «AI-ассистент» на шаге разъёма -> выход (не стал «типом разъёма»)")

    # 5) не-текст во время ввода — подсказка, без падения на message.text.strip()
    ctx = await new_ctx()
    await ctx.set_state(BuildState.waiting_for_config)
    msg = Msg(None)
    await build.process_build_not_text(msg)
    assert any("14S12P" in t for t in msg.sent), msg.sent
    print("[OK] не-текст во время ввода конфигурации -> подсказка без падения")

    # 6) feedback: кнопка меню не уходит администратору
    ctx = await new_ctx()
    await ctx.set_state(FeedbackState.waiting_for_feedback)
    msg = Msg("🔧 Своя сборка")
    await menu.process_feedback(msg, ctx)
    assert await ctx.get_state() is None
    assert any("Обратная связь закрыта" in t for t in msg.sent), msg.sent
    print("[OK] кнопка меню во время обратной связи -> выход, а не отправка админу")

    # 7) новый хендлер кнопки «🤖 AI-ассистент»
    ctx = await new_ctx()
    await ctx.set_state(BuildState.waiting_for_config)
    msg = Msg("🤖 AI-ассистент")
    await menu.button_ai_assistant(msg, ctx)
    assert await ctx.get_state() is None
    assert any("AI-ассистент" in t and "Напишите" in t for t in msg.sent), msg.sent
    print("[OK] хендлер кнопки «AI-ассистент» -> чистит FSM и просит написать вопрос")

    # 8) FIX «странного поведения»: роутер early_buttons стоит в bot.dp раньше
    #    FSM-сценариев и регистрирует те же реализации — «🤖 AI-ассистент» и
    #    «📚 Шпаргалки» открываются прямо из сценария, а не съедаются как
    #    «выход в меню» (калькулятор: «Калькулятор закрыт» без раздела)
    #    НОВОЕ (аудит 3.1–3.2): admin-роутер — первый (приоритет /stats и
    #    FSM-команд админа), early_buttons — сразу за ним.
    import bot as bot_mod
    from handlers import admin, cheatsheets, early_buttons
    subs = bot_mod.dp.sub_routers
    names = [getattr(r, "name", None) for r in subs]
    assert subs[0] is admin.router, names
    assert subs[1] is early_buttons.router, names
    callbacks = [h.callback for h in early_buttons.router.message.handlers]
    assert menu.button_ai_assistant in callbacks, callbacks
    assert cheatsheets.button_cheatsheets in callbacks, callbacks
    print("[OK] early_buttons: «🤖 AI-ассистент» и «📚 Шпаргалки» раньше FSM-сценариев (admin — первый)")


asyncio.run(main())