"""
Точка входа FPV Sales Bot.

Логика разложена по модулям (зависимости строго в одну сторону, без циклов):
  config.py / logging_setup.py — .env-переменные, логгеры, экземпляр Bot;
  data/      — каталог моделей АКБ, справочники фильтров, сценарии продаж,
               база возражений;
  states/    — FSM-состояния;
   db/        — SQLite-хранилище (follow-up, история расчётов, прайс, FAQ,
                настройки): движок, репозитории, первичный перенос из JSON;
  keyboards/ — клавиатуры;
  utils/     — фильтрация, форматирование, отправка карточек;
  services/  — сервис-слой: ai_providers (провайдеры AI, выбор через AI_PROVIDER в .env);
  ai/        — фасад над AI-провайдером: ответы, feedback-кнопки, логирование;
  handlers/  — роутеры: admin (статистика и CRUD), calculator, menu,
                cheatsheets («📚 Шпаргалки» — inline-меню справочных разделов),
                scenarios, followup (включая reminders_loop — фоновая утренняя
                сводка о follow-up, и upcoming_loop — напоминания «за 10/5/2
                минуты» до события), faq, objections, selection, search, build,
                builder («🛠 Конструктор дронов» — FSM-сборка по слотам,
                совместимость, черновики), voice, fallback.

Запуск: python bot.py (из корня проекта).
"""

import asyncio
import contextlib
import os
from datetime import datetime
from pathlib import Path

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import bot, system_logger
from db import close_db, init_db
from handlers.admin import router as admin_router
from handlers.early_buttons import router as early_buttons_router
from handlers.build import router as build_router
from handlers.builder import router as builder_router
from handlers.calculator import router as calculator_router
from handlers.cheatsheets import router as cheatsheets_router
from handlers.fallback import router as fallback_router
from handlers.faq import router as faq_router
from handlers.followup import reminders_loop, router as followup_router, upcoming_loop
from handlers.menu import router as menu_router
from handlers.objections import router as objections_router
from handlers.scenarios import router as scenarios_router
from handlers.search import router as search_router
from handlers.selection import router as selection_router
from handlers.voice import router as voice_router
from services.ai_providers import get_ai_provider

dp = Dispatcher(storage=MemoryStorage())

# Порядок регистрации роутеров (контракт tests/test_verify_all.py):
#   0) admin — админ-команды и шаги админ-FSM раньше FSM-сценариев и fallback
#      (калькулятор ловит любой текст в своих состояниях);
#   1) early_buttons — кнопки «🤖 AI-ассистент» и «📚 Шпаргалки» открываются из
#      любого сценария: нажатие завершает активный FSM и сразу открывает раздел
#      (раньше кнопку съедал сценарий — калькулятор отвечал «расчёт закрыт»,
#      а раздел не открывался);
#   2) calculator — FSM-ввод не должен перехватываться fallback'ом AI;
#   3) menu — callback "model:<код>" из сценариев обрабатывается здесь;
#   4) cheatsheets — «📚 Шпаргалки» (cheat:*): сразу после menu, до fallback;
#   5) scenarios — после menu: кнопки «📦 Карточка <модель>» перехватывает menu;
#   6) followup — FSM ограничен FollowUpState, но обязан стоять до fallback;
#   7) faq — статические экраны + FSM поиска;
#   8) objections — статические экраны + стоп-лист; до fallback;
#   9) selection / search / build / builder / voice — прочие сценарии;
#  10) fallback — последним: догоняет всё, что не распознано выше.
dp.include_router(admin_router)
dp.include_router(early_buttons_router)
dp.include_router(calculator_router)
dp.include_router(menu_router)
dp.include_router(cheatsheets_router)
dp.include_router(scenarios_router)
dp.include_router(followup_router)
dp.include_router(faq_router)
dp.include_router(objections_router)
dp.include_router(selection_router)
dp.include_router(search_router)
dp.include_router(build_router)
# ЭТАП 6: «🛠 Конструктор» (/builder, FSM BuilderState, callback "b:") — FSM-сценарий,
# обязан стоять до fallback (иначе текстовый ввод part_input уйдёт в AI).
dp.include_router(builder_router)
dp.include_router(voice_router)
dp.include_router(fallback_router)


# ============================================================
# Heartbeat для серверного watchdog (deploy/watchdog.py, раздел 5 README):
# бот раз в HEARTBEAT_INTERVAL секунд пишет метку времени в HEARTBEAT_FILE
# (переменная .env; пустое значение — задача отключена). Watchdog считает
# метку просроченной позже HEARTBEAT_MAX_AGE (по умолчанию 900 с) и рестартует
# юнит. Плохой путь (нет прав, путь сквозь файл) не должен ронять бот.
# ============================================================
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "").strip()
try:
    HEARTBEAT_INTERVAL = float(os.getenv("HEARTBEAT_INTERVAL", "") or 60)
except ValueError:
    HEARTBEAT_INTERVAL = 60.0


def _write_heartbeat() -> None:
    """Одна метка: ISO-время в HEARTBEAT_FILE (недостающие каталоги создаются)."""
    if not HEARTBEAT_FILE:
        return
    try:
        path = Path(HEARTBEAT_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except OSError as e:
        system_logger.warning(f"Heartbeat: не удалось записать {HEARTBEAT_FILE}: {e}")


async def heartbeat_loop():
    """Фоновая задача main(): метка сразу после старта и далее с периодом
    HEARTBEAT_INTERVAL секунд; без HEARTBEAT_FILE — выходит сразу."""
    if not HEARTBEAT_FILE:
        return
    while True:
        _write_heartbeat()
        await asyncio.sleep(HEARTBEAT_INTERVAL if HEARTBEAT_INTERVAL > 0 else 60.0)


async def main():
    system_logger.info("=" * 50)
    system_logger.info("Bot starting...")
    system_logger.info("=" * 50)

    # БД: движок + таблицы + первичный перенос JSON-справочников (идемпотентно).
    # До reminders_loop — он уже читает follow-up из таблиц.
    await init_db()

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

    # Система напоминаний о follow-up: ЭТАП 2 — фоновая утренняя сводка в 9:00,
    # ЭТАП 3 — сканер предварительных напоминаний «за 10/5/2 минуты».
    # Задачи по одной на процесс и живут независимо от переподключений polling.
    reminders_task = asyncio.create_task(reminders_loop(bot))
    upcoming_task = asyncio.create_task(upcoming_loop(bot))
    heartbeat_task = asyncio.create_task(heartbeat_loop())
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
        for task in (reminders_task, upcoming_task, heartbeat_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await close_db()
        # Сессию бота (с прокси-коннектором или без) закрываем сами: при
        # рестартах polling в цикле выше соединение могло остаться открытым.
        with contextlib.suppress(Exception):
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())