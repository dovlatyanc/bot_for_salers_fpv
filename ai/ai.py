import json
from datetime import datetime

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import dialog_logger, system_logger
from services.ai_providers import AIProviderError, get_ai_provider
from utils import split_text


async def ask_ai(question: str) -> str:
    """Отвечает на вопрос через AI-провайдера, выбранного в .env (AI_PROVIDER).

    HTTP-детали (эндпоинты, аутентификация, форматы) инкапсулированы
    в services/ai_providers; здесь только превращение ошибок провайдера
    в текст ответа, чтобы пользователь всегда получил реакцию.
    """
    try:
        return await get_ai_provider().ask(question)
    except AIProviderError as e:
        return str(e)


async def answer_ai_with_feedback(message: types.Message, text: str):
    feedback_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Ответ помог", callback_data="fb_good"),
        InlineKeyboardButton(text="👎 Неточно",    callback_data="fb_bad"),
    ]])
    parts = split_text(text)
    for i, part in enumerate(parts):
        kb = feedback_kb if i == len(parts) - 1 else None
        try:
            await message.answer(part, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            system_logger.warning(f"Failed to parse HTML: {e}")
            await message.answer(part, reply_markup=kb)


async def handle_ai_request(message: types.Message, question: str):
    thinking = await message.answer("⏳ <i>Запрашиваю AI-ассистента...</i>", parse_mode="HTML")
    answer = await ask_ai(question)
    try:
        await thinking.delete()
    except Exception:
        pass

    dialog_logger.info(json.dumps({
        'event': 'bot_response',
        'user_id': message.from_user.id,
        'username': message.from_user.username or 'unknown',
        'original_question': question,
        'answer': answer[:500],
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))

    await answer_ai_with_feedback(message, answer)