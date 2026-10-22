"""
User-tracking middleware (аудит/развитие п. 3.1): учёт пользователей бота.

При любом входящем событии от пользователя (сообщение или нажатие inline-кнопки)
регистрирует его в таблице users (пакет db): новое user_id → INSERT (аналог
INSERT OR IGNORE), повторное — обновление last_seen_at. На этих данных команда
/stats (handlers/admin.py) показывает «всего пользователей» и «активных за 7 дней».

Регистрация (bot.py, до include_router):
    dp.message.outer_middleware(UserTrackingMiddleware())
    dp.callback_query.outer_middleware(UserTrackingMiddleware())

Middleware внешнему (outer): срабатывает до фильтров и FSM-хендлеров, чтобы
учитывать и «неопознанные» сообщения (fallback). Учёт не должен ломать работу
бота: любая ошибка БД гасится с записью в системный лог, обработка события
продолжается. События без from_user и от ботов игнорируются.
"""

import logging

from aiogram import BaseMiddleware

from db import users as users_db

system_logger = logging.getLogger("system")


class UserTrackingMiddleware(BaseMiddleware):
    """Регистрирует/освежает пользователя в таблице users перед хендлером."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is not None and not getattr(user, "is_bot", False):
            try:
                is_new = await users_db.touch(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                )
                if is_new:
                    system_logger.info(
                        f"New user: {user.id} (@{user.username or '-'}) "
                        f"{user.first_name or ''}"
                    )
            except Exception as e:   # учёт не должен ронять обработку события
                system_logger.warning(f"User tracking failed for {user.id}: {e}")
        return await handler(event, data)