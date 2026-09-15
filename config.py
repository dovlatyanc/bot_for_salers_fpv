"""
Конфигурация приложения: переменные окружения, логгеры и единый экземпляр Bot.

Все модули импортируют только config (зависимости строго в одну сторону:
config ← data/states/keyboards/utils/ai ← handlers ← bot.py-точка входа),
поэтому циклических импортов нет, а объект bot во всём приложении один.
"""

import logging
import os
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

from logging_setup import setup_logging

# Корень приложения (каталог config.py) — от него считаются относительные пути
# (фото моделей в MODELS), независимо от текущего каталога запуска.
BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

setup_logging(
    system_log=BASE_DIR / "bot_system.log",
    dialog_log=BASE_DIR / "bot_dialogs.log",
)
system_logger = logging.getLogger("system")
dialog_logger = logging.getLogger("dialog")

# ============================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
AI_PROXY_URL = os.getenv("AI_PROXY_URL", "").strip()
AI_PROXY_TOKEN = os.getenv("AI_PROXY_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PROXY_URL = os.getenv("PROXY_URL", "").strip()

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан. Проверьте файл .env")

# Создаем бота с прокси, если указан
if PROXY_URL:
    bot = Bot(token=BOT_TOKEN, session=AiohttpSession(proxy=PROXY_URL))
    system_logger.info(f"Bot started with proxy: {PROXY_URL}")
else:
    bot = Bot(token=BOT_TOKEN)
    system_logger.info("Bot started without proxy")
