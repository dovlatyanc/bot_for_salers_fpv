"""
Точка входа FPV Sales Bot.

Логика разложена по модулям (зависимости строго в одну сторону, без циклов):
  config.py / logging_setup.py — .env-переменные, логгеры, экземпляр Bot;
  data/      — каталог моделей АКБ, справочники фильтров, сценарии продаж,
               база возражений;
  states/    — FSM-состояния;
  keyboards/ — клавиатуры;
  utils/     — фильтрация, форматирование, отправка карточек;
  services/  — сервис-слой: ai_providers (провайдеры AI, выбор через AI_PROVIDER в .env);
  ai/        — фасад над AI-провайдером: ответы, feedback-кнопки, логирование;
  handlers/  — роутеры: calculator, menu, scenarios, followup (включая
               reminders_loop — фоновая утренняя сводка о follow-up),
               faq, objections, selection, search, build, voice, fallback.

Запуск: python bot.py (из корня проекта).
"""

import asyncio
import contextlib

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import bot, system_logger
from handlers.build import router as build_router
from handlers.calculator import router as calculator_router
from handlers.fallback import router as fallback_router
from handlers.faq import router as faq_router
from handlers.followup import reminders_loop, router as followup_router
from handlers.menu import router as menu_router
from handlers.objections import router as objections_router
from handlers.scenarios import router as scenarios_router
from handlers.search import router as search_router
from handlers.selection import router as selection_router
from handlers.voice import router as voice_router
from services.ai_providers import get_ai_provider

dp = Dispatcher(storage=MemoryStorage())

# Порядок регистрации роутеров:
#   1) calculator — FSM-ввод не должен перехватываться fallback'ом AI;
#   2) menu — callback "model:<код>" из сценариев обрабатывается здесь;
#   3) scenarios — после menu: кнопки «📦 Карточка <модель>» перехватывает menu;
#   4) followup — FSM ограничен FollowUpState, но обязан стоять до fallback;
#   5) faq — статические экраны + FSM поиска;
#   6) objections — статические экраны + стоп-лист; до fallback;
#   7) selection / search / build / voice — прочие сценарии;
#   8) fallback — последним: догоняет всё, что не распознано выше.
dp.include_router(calculator_router)
dp.include_router(menu_router)
dp.include_router(scenarios_router)
dp.include_router(followup_router)
dp.include_router(faq_router)
dp.include_router(objections_router)
dp.include_router(selection_router)
dp.include_router(search_router)
dp.include_router(build_router)
dp.include_router(voice_router)
dp.include_router(fallback_router)


async def main():
    system_logger.info("=" * 50)
    system_logger.info("Bot starting...")
    system_logger.info("=" * 50)

    # Fail-fast: неверный AI_PROVIDER должен уронить старт,
    # а не первое сообщение пользователя.
    provider = get_ai_provider()
    system_logger.info(
        f"AI provider: {provider.name}, configured: {provider.is_configured}"
    )
    if not provider.is_configured:
        system_logger.warning(
            "AI provider не сконфигурирован — fallback будет возвращать "
            "подсказку. Статические разделы (FAQ, Сценарии, Возражения, "
            "Подбор) работают независимо."
        )

    # Система напоминаний о follow-up (ЭТАП 2): фоновая утренняя сводка в 9:00.
    # Задача одна на процесс и живёт независимо от переподключений polling.
    reminders_task = asyncio.create_task(reminders_loop(bot))
    retry_delay, max_retry_delay = 5, 60
    try:
        while True:
            try:
                await dp.start_polling(bot)
                break
            except Exception as e:
                system_logger.error(f"Polling error: {e}")
                system_logger.info(f"Restarting in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, max_retry_delay)
    finally:
        reminders_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reminders_task


if __name__ == "__main__":
    asyncio.run(main())