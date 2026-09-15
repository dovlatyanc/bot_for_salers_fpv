import json
from datetime import datetime

import aiohttp
from aiogram import F, Router, types

from bot import bot, AI_PROXY_URL, AI_PROXY_TOKEN, dialog_logger, system_logger
from ai import handle_ai_request

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

        headers = {
            "Authorization": f"Bearer {AI_PROXY_TOKEN}",
            "Content-Type": "application/octet-stream",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{AI_PROXY_URL}/transcribe", data=file_bytes, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    recognized_text = data.get("text", "").strip()
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
                else:
                    error_text = await resp.text()
                    system_logger.error(f"Voice recognition error: {resp.status} - {error_text[:100]}")
                    await thinking.edit_text(f"Ошибка распознавания: {resp.status}\n{error_text[:100]}")
    except Exception:
        system_logger.exception("Voice processing failed")
        try:
            await thinking.edit_text("Произошла ошибка при обработке голосового сообщения.")
        except Exception:
            pass