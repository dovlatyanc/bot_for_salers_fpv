"""
Точка входа FPV Sales Bot.

Логика разложена по модулям (зависимости строго в одну сторону, без циклов):
  config.py / logging_setup.py — .env-переменные, логгеры, экземпляр Bot;
  data/      — каталог моделей АКБ, справочники фильтров, сценарии продаж;
  states/    — FSM-состояния;
  keyboards/ — клавиатуры;
  utils/     — фильтрация, форматирование, отправка карточек;
  ai/        — запросы к AI-прокси;
  handlers/  — роутеры: calculator, menu, scenarios, selection, search, build, voice, fallback.

Запуск: python bot.py (из корня проекта).
"""

import asyncio

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import bot, system_logger
from handlers.build import router as build_router
from handlers.calculator import router as calculator_router
from handlers.fallback import router as fallback_router
from handlers.menu import router as menu_router
from handlers.scenarios import router as scenarios_router
from handlers.search import router as search_router
from handlers.selection import router as selection_router
from handlers.voice import router as voice_router

dp = Dispatcher(storage=MemoryStorage())

# calculator первым: его FSM-ввод не должен перехватываться fallback'ом AI;
# fallback последним: догоняет всё, что не распознано роутерами выше.
dp.include_router(calculator_router)
dp.include_router(menu_router)
# scenarios после menu: кнопки «📦 Карточка <модель>» в сценариях обрабатывает
# роутер menu (callback "model:") — он должен стоять раньше scenarios.
dp.include_router(scenarios_router)
dp.include_router(selection_router)
dp.include_router(search_router)
dp.include_router(build_router)
dp.include_router(voice_router)
dp.include_router(fallback_router)


async def main():
    system_logger.info("=" * 50)
    system_logger.info("Bot starting...")
    system_logger.info("=" * 50)

    retry_delay, max_retry_delay = 5, 60
    while True:
        try:
            await dp.start_polling(bot)
            break
        except Exception as e:
            system_logger.error(f"Polling error: {e}")
            system_logger.info(f"Restarting in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, max_retry_delay)


if __name__ == "__main__":
    asyncio.run(main())
