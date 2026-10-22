"""
Конфигурация приложения: переменные окружения, логгеры и единый экземпляр Bot.

Все модули импортируют только config (зависимости строго в одну сторону:
config ← data/states/keyboards/utils/ai/services ← handlers ← bot.py-точка входа),
поэтому циклических импортов нет, а объект bot во всём приложении один.
"""

import logging
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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
# Провайдер AI: cloudflare (Cloudflare AI Worker) | yandex (YandexGPT + SpeechKit)
AI_PROVIDER = os.getenv("AI_PROVIDER", "cloudflare").strip().lower()
# --- YandexGPT (при AI_PROVIDER=yandex) ---
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_IAM_TOKEN = os.getenv("YANDEX_IAM_TOKEN", "").strip()
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "").strip()
YANDEX_MODEL = os.getenv("YANDEX_MODEL", "yandexgpt").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PROXY_URL = os.getenv("PROXY_URL", "").strip()

# --- Heartbeat для серверного watchdog (deploy/watchdog.py): фоновая задача
# в bot.py пишет метку времени в этот файл каждые HEARTBEAT_INTERVAL секунд;
# просроченный файл (старше HEARTBEAT_MAX_AGE в watchdog) — признак зависшего
# цикла событий, watchdog перезапускает сервис. Путь не задан — выключено.
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "").strip()
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "60"))

# --- База данных (пакет db) ---
# SQLAlchemy URL. Пусто — SQLite: файл data/bot.db (путь подставит db.engine).
# Переход на PostgreSQL позже — только смена строки и установка asyncpg:
#   DB_URL=postgresql+asyncpg://fpv:password@localhost:5432/fpv_bot
DB_URL = os.getenv("DB_URL", "").strip()


# --- SOCKS-прокси (aiohttp нативно понимает только http/https) ---
_SOCKS_SCHEMES = frozenset({"socks4", "socks4a", "socks5", "socks5h"})


def mask_proxy_url(url: str) -> str:
    """Маскирует логин/пароль в URL прокси: socks5://user:pass@host:1080 ->
    socks5://***@host:1080 (для безопасного логирования)."""
    try:
        parsed = urlsplit(url)
        if parsed.username is None and parsed.password is None:
            return url
        netloc = f"***@{parsed.hostname}" + (
            f":{parsed.port}" if parsed.port is not None else ""
        )
        return urlunsplit(
            (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
        )
    except ValueError:
        return "<некорректный PROXY_URL>"


def make_bot_session():
    """Сессия бота с прокси из PROXY_URL.

    Пустая/незаданная PROXY_URL -> None (бот создаётся с дефолтной сессией).
    SOCKS-схемы (socks4/socks4a/socks5/socks5h) и http/https оба передаются
    в AiohttpSession(proxy=...): aiogram сам строит ProxyConnector из пакета
    aiohttp-socks (добавлен в requirements.txt). Другая схема -> SystemExit
    с понятной ошибкой.
    """
    url = (PROXY_URL or "").strip()
    if not url:
        return None
    scheme = url.split("://", 1)[0].lower()
    if scheme not in _SOCKS_SCHEMES | {"http", "https"}:
        raise SystemExit(
            f"PROXY_URL: неподдерживаемая схема «{scheme}» "
            "(можно socks5://, socks4://, http://)"
        )
    return AiohttpSession(proxy=url)


if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан. Проверьте файл .env")

_bot_session = make_bot_session()
if _bot_session is not None:
    bot = Bot(token=BOT_TOKEN, session=_bot_session)
    system_logger.info(f"Bot started with proxy: {mask_proxy_url(PROXY_URL)}")
else:
    bot = Bot(token=BOT_TOKEN)
    system_logger.info("Bot started without proxy")
