import json
from datetime import datetime

from aiogram import F, Router, types

from config import ADMIN_ID, dialog_logger
from keyboards import MAIN_BUTTONS
from ai import handle_ai_request
from services.ai_providers import get_ai_provider
from services.ai_providers.resilience import ResilientAIProvider, get_breaker

router = Router()


@router.message(lambda m: m.text and m.text.startswith("/health"))
async def health_command(message: types.Message):
    """🩺 Живой статус AI-провайдера — только для админа (ADMIN_ID в .env).

    Отчёт: конфигурация провайдера, состояние circuit breaker (сбои, последняя
    ошибка) и живая проба ask('ping') напрямую через провайдера — мимо ретраев
    и брейкера, чистое наблюдение. Не-админ получает тишину — как и любые
    прочие команды (unknown_command ниже).
    """
    if not (message.from_user and message.from_user.id == ADMIN_ID):
        return
    provider = ResilientAIProvider(get_ai_provider(), breaker=get_breaker())
    await message.answer(f"🩺 AI health\n{await provider.health_check()}")


@router.message(lambda m: m.text and not m.text.startswith("/") and m.text not in MAIN_BUTTONS)
async def fallback_ai(message: types.Message):
    dialog_logger.info(json.dumps({
        'event': 'user_request',
        'user_id': message.from_user.id,
        'username': message.from_user.username or 'unknown',
        'message_id': message.message_id,
        'text': message.text,
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))
    await handle_ai_request(message, message.text)


@router.message(lambda m: m.text and m.text.startswith("/"))
async def unknown_command(message: types.Message):
    pass


@router.message()
async def unsupported(message: types.Message):
    await message.answer("Пока я понимаю только текст и голосовые сообщения.")