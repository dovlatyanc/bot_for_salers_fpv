"""UI-компоненты контроля доступа: текст запроса + кнопки решения (для админа).

Отдельный модуль keyboards/access.py (а не handlers/access.py), потому что
уведомление о запросе отправляет AccessControlMiddleware (middleware.py):
направление зависимостей middleware → handlers запрещено, а keyboards —
нижний слой, доступный и middleware, и хендлерам.
"""

import html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

APPROVE_PREFIX = "access:approve:"
DECLINE_PREFIX = "access:decline:"


def text_access_request(user) -> str:
    """Текст уведомления админу о новом запросе доступа (без данных бота)."""
    name = html.escape(str(getattr(user, "full_name", None) or "—"))
    username = f"@{user.username}" if getattr(user, "username", None) else "ник нет"
    return (
        "🔐 <b>Запрос доступа</b>\n"
        f"ID: <code>{user.id}</code>\n"
        f"Ник: {username}\n"
        f"Имя: {name}"
    )


def kb_access_decision(user_id: int) -> InlineKeyboardMarkup:
    """Кнопки решения: ✅ Одобрить / ❌ Отклонить (callback access:*:<id>)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить",
                             callback_data=f"{APPROVE_PREFIX}{user_id}"),
        InlineKeyboardButton(text="❌ Отклонить",
                             callback_data=f"{DECLINE_PREFIX}{user_id}"),
    ]])
