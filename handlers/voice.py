import json
from datetime import datetime

from aiogram import F, Router, types

from config import bot, dialog_logger, system_logger
from ai import handle_ai_request
from services.ai_providers import AIProviderError, get_ai_provider

router = Router()


@router.message(F.voice)
async def process_voice(message: types.Message):
    dialog_logger.info(json.dumps({
        'event': 'user_voice_request',
        'user_id': message.from_user.id,
        'username': message.from_user.username or 'unknown',
        'message_id': message.message_id,
        'duration': message.voice.duration,
        'file_size': message.voice.file_size,
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))

    if message.voice.file_size > 20 * 1024 * 1024:
        await message.answer("Голосовое сообщение слишком большое (макс 20 МБ).")
        return

    thinking = await message.answer("🎧 Распознаю речь...")
    try:
        file = await bot.get_file(message.voice.file_id)
        file_bytes_io = await bot.download_file(file.file_path)
        file_bytes = file_bytes_io.read()

        # Распознавание речи через активного AI-провайдера (см. AI_PROVIDER в .env).
        try:
            recognized_text = await get_ai_provider().transcribe(file_bytes)
        except AIProviderError as e:
            detail = (e.body or str(e)).strip()[:100]
            system_logger.error(f"Voice recognition error: {e.status} - {detail}")
            status_prefix = f"{e.status}\n" if e.status else ""
            await thinking.edit_text(f"Ошибка распознавания: {status_prefix}{detail}")
            return

        if recognized_text:
            await thinking.delete()
            dialog_logger.info(json.dumps({
                'event': 'voice_recognized',
                'user_id': message.from_user.id,
                'recognized_text': recognized_text,
                'timestamp': datetime.now().isoformat()
            }, ensure_ascii=False))
            await message.answer(
                f"🗣 <b>Распознано:</b>\n<i>{recognized_text}</i>",
                parse_mode="HTML",
            )
            await handle_ai_request(message, recognized_text)
        else:
            await thinking.edit_text("Не удалось распознать речь.")
    except Exception:
        system_logger.exception("Voice processing failed")
        try:
            await thinking.edit_text("Произошла ошибка при обработке голосового сообщения.")
        except Exception:
            pass