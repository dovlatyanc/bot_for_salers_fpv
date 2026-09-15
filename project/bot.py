import asyncio
import os
from dotenv import load_dotenv
import logging
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession

load_dotenv()

# ---------- ЛОГИ ----------
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

system_logger = logging.getLogger('system')
system_logger.setLevel(logging.INFO)
dialog_logger = logging.getLogger('dialog')
dialog_logger.setLevel(logging.INFO)

sh = RotatingFileHandler('bot_system.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
sh.setFormatter(log_formatter); system_logger.addHandler(sh)
dh = RotatingFileHandler('bot_dialogs.log', maxBytes=20*1024*1024, backupCount=10, encoding='utf-8')
dh.setFormatter(log_formatter); dialog_logger.addHandler(dh)

ch = logging.StreamHandler(); ch.setFormatter(log_formatter); system_logger.addHandler(ch)
logging.basicConfig(level=logging.INFO)

# ---------- КОНФИГ ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
AI_PROXY_URL = os.getenv("AI_PROXY_URL", "").strip()
AI_PROXY_TOKEN = os.getenv("AI_PROXY_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PROXY_URL = os.getenv("PROXY_URL", "").strip()

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан. Проверьте .env")

if PROXY_URL:
    bot = Bot(token=BOT_TOKEN, session=AiohttpSession(proxy=PROXY_URL))
    system_logger.info(f"Bot started with proxy: {PROXY_URL}")
else:
    bot = Bot(token=BOT_TOKEN)
    system_logger.info("Bot started without proxy")

dp = Dispatcher(storage=MemoryStorage())

# ---------- ПОДКЛЮЧЕНИЕ ХЕНДЛЕРОВ ----------
# calculator первым, чтобы FSM-ввод не перехватывался fallback'ом AI
from handlers.calculator import router as calculator_router
from handlers.menu import router as menu_router
from handlers.selection import router as selection_router
from handlers.build import router as build_router
from handlers.voice import router as voice_router
from handlers.fallback import router as fallback_router  # последним

dp.include_router(calculator_router)
dp.include_router(menu_router)
dp.include_router(selection_router)
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