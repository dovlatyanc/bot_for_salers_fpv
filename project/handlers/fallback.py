import json
from datetime import datetime

from aiogram import F, Router, types

from bot import dialog_logger
from keyboards import MAIN_BUTTONS
from ai import handle_ai_request

router = Router()


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