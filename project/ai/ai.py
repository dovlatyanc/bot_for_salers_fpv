import aiohttp
import json
from datetime import datetime

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import AI_PROXY_URL, AI_PROXY_TOKEN, dialog_logger, system_logger
from utils import split_text


async def ask_ai(question: str) -> str:
    if not AI_PROXY_URL:
        system_logger.warning("AI_PROXY_URL not configured")
        return "AI-ассистент ещё не подключён. Заполните AI_PROXY_URL и AI_PROXY_TOKEN в .env"

    headers = {"Content-Type": "application/json"}
    if AI_PROXY_TOKEN:
        headers["Authorization"] = f"Bearer {AI_PROXY_TOKEN}"

    payload = {"question": question, "source": "python-telegram-bot"}
    timeout = aiohttp.ClientTimeout(total=90)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(AI_PROXY_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    system_logger.error(f"AI proxy returned {resp.status}: {body[:500]}")
                    return f"Cloudflare AI вернул ошибку {resp.status}:\n{body[:500]}"
                data = await resp.json()
                answer = data.get("answer")
                if not answer:
                    system_logger.warning("AI returned empty answer")
                    return "AI вернул пустой ответ."
                return answer
    except Exception as e:
        system_logger.exception("AI request failed")
        return f"Ошибка запроса к AI: {e}"


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