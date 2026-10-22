import json
import re
from datetime import datetime

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import dialog_logger, system_logger
from db import counters
from services.ai_providers import AIProviderError, get_ai_provider
from services.ai_providers.prompts import ai_fallback_phrase
from services.ai_providers.resilience import ResilientAIProvider, get_breaker
from utils import split_text

#: Маркер стартовой секции в формате ответа ассистента (формат задаёт промт
#: прокси fpv-ai-proxy: «Начинай ответ СТРОГО с тега <b>💡 Вывод:</b>»).
SECTION_MARKER = "💡 Вывод:"

#: Технические фразы, протёкшие в «успешный» ответ — признак сбоя бэкенда
#: (невалидный/просроченный ключ, отказ провайдера): в чат не показываем.
_BROKEN_ANSWER_RE = re.compile(
    r"unauthorized|unauthenticated|forbidden|invalid api key"
    r"|api key (is )?(invalid|expired|wrong)|authentication (error|failed)"
    r"|auth error|quota|rate limit|internal (server )?error|bad gateway"
    r"|service unavailable|gateway time-?out"
    r"|ai proxy returned|cloudflare ai вернул ошибку|ошибка запроса к ai"
    r"|ошибка авторизации|не авторизован|неверный (ключ|токен)",
    re.IGNORECASE,
)


def answer_is_broken(answer: str) -> str | None:
    """Определяет «мусорный» ответ AI; возвращает причину сбоя или None.

    Считаем сбоем:
      * деградацию модели — маркер секции «💡 Вывод:» встречается больше
        одного раза (зацикливание с пустыми секциями) либо ответ обрывается
        на маркере без содержимого;
      * протёкший технический текст ошибки (невалидный ключ, отказ бэкенда).
    Штатный ответ (один маркер + содержимое секций) проходит как есть.
    """
    text = (answer or "").strip()
    if not text:
        return "пустой ответ"
    if text.count(SECTION_MARKER) > 1:
        return "деградация модели: маркер секции повторяется"
    if re.search(r"💡 Вывод:?(?:</b>)?\s*$", text):
        return "деградация модели: ответ обрывается на пустой секции"
    match = _BROKEN_ANSWER_RE.search(text)
    if match:
        return f"технический текст ошибки в ответе: {match.group(0)!r}"
    return None


async def ask_ai(question: str) -> str:
    """Отвечает на вопрос через AI-провайдера, выбранного в .env (AI_PROVIDER).

    Вызов идёт через обёртку ResilientAIProvider (services/ai_providers/
    resilience.py): retry переходящих ошибок + circuit breaker. Пользователь
    всегда получает текст: ответ провайдера, его подсказку о настройке или
    случайную дружелюбную фразу-заглушку (ai_fallback_phrase, prompts.py)
    при сбое — техническая деталь при этом уходит целиком в системный лог,
    а не в чат менеджерам. HTTP-успех не гарантия качества: «мусорный» ответ
    (деградация модели или протёкший текст ошибки при невалидном ключе,
    см. answer_is_broken) заменяется такой же фразой-заглушкой.

    Каждое обращение учитывается дневным счётчиком 'ai_ask' (daily_counters,
    db.repositories) — команда /stats показывает «обращений к AI сегодня».
    Учёт нефатален: сбой БД гасится с записью в системный лог.
    """
    try:
        await counters.incr("ai_ask")
    except Exception as e:
        system_logger.warning(f"AI usage counter not written: {e}")
    try:
        provider = ResilientAIProvider(get_ai_provider(), breaker=get_breaker())
        answer = await provider.ask(question)
    except AIProviderError as e:
        system_logger.error(f"AI ask failed after retries: {e}")
        return ai_fallback_phrase()
    reason = answer_is_broken(answer)
    if reason:
        system_logger.error(f"AI answer rejected ({reason}): {answer[:500]}")
        return ai_fallback_phrase()
    return answer


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