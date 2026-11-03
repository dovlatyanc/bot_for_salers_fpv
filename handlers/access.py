"""Подтверждение доступа: inline-кнопки «✅ Одобрить» / «❌ Отклонить» у админа.

Запросы создаёт AccessControlMiddleware (middleware.py) при первом касании
бота новым пользователем; UI уведомления (текст + кнопки) — keyboards/access.py
(нижний слой: middleware не может импортировать handlers). Решение по кнопке
ставится в allowed_users (db.allowed):

  access:approve:<id> — status="approved"  → пользователю «доступ открыт»;
  access:decline:<id> — status="declined"  → пользователю нейтральный отказ.

callback_data короткий («access:approve:123456789» ≈ 25 байт) — запас до
лимита Telegram 64 байта. Кнопку жмёт ТОЛЬКО ADMIN_ID (иначе «Нет прав»);
повторное нажатие не ошибка — карточка просто перерисовывается.

Роутер регистрируется в bot.py СРАЗУ ПОСЛЕ early_buttons, ДО calculator/menu/
сценариев: callback "access:*" не должен перехватываться ничем другим.
"""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import ADMIN_ID
from db import allowed
from keyboards.access import APPROVE_PREFIX, DECLINE_PREFIX
from utils.ui import safe_answer

router = Router(name="access")


def _parse_id(callback_data: str, prefix: str) -> int | None:
    """user_id из callback_data (после префикса); None — битое/чужое значение."""
    raw = callback_data[len(prefix):]
    try:
        return int(raw)
    except ValueError:
        return None



def _is_admin(query: CallbackQuery) -> bool:
    """Кнопки решения жмёт только ADMIN_ID (0/не задан — никому)."""
    return bool(ADMIN_ID) and query.from_user is not None \
        and query.from_user.id == ADMIN_ID


@router.callback_query(F.data.startswith(APPROVE_PREFIX))
async def cb_access_approve(callback: CallbackQuery) -> None:
    """✅ Одобрить: статус в БД → уведомление пользователю → перерисовка карточки."""
    if not _is_admin(callback):
        await safe_answer(callback, "Нет прав", show_alert=True)
        return
    user_id = _parse_id(callback.data or "", APPROVE_PREFIX)
    if user_id is None:
        await safe_answer(callback, "Некорректная кнопка", show_alert=True)
        return
    if await allowed.set_decision(user_id, "approved"):
        await _notify_user(callback.bot, user_id, "✅ Администратор одобрил вам доступ.")
        await _edit_card(callback, f"✅ Одобрен <code>{user_id}</code>")
    else:
        await _edit_card(callback, f"✅ Одобрен <code>{user_id}</code> (записи не было)")


@router.callback_query(F.data.startswith(DECLINE_PREFIX))
async def cb_access_decline(callback: CallbackQuery) -> None:
    """❌ Отклонить: статус в БД → нейтральное уведомление → перерисовка карточки."""
    if not _is_admin(callback):
        await safe_answer(callback, "Нет прав", show_alert=True)
        return
    user_id = _parse_id(callback.data or "", DECLINE_PREFIX)
    if user_id is None:
        await safe_answer(callback, "Некорректная кнопка", show_alert=True)
        return
    if await allowed.set_decision(user_id, "declined"):
        await _notify_user(callback.bot, user_id, "⛔ У вас нет доступа.")
        await _edit_card(callback, f"❌ Отклонён <code>{user_id}</code>")
    else:
        await _edit_card(callback, f"❌ Отклонён <code>{user_id}</code> (записи не было)")


@router.callback_query(F.data.startswith("access:"))
async def cb_access_unknown(callback: CallbackQuery) -> None:
    """Протухшие/битые access:* кнопки — страховка (регистрация последней)."""
    await safe_answer(callback, "Кнопка устарела", show_alert=True)


async def _notify_user(bot, user_id: int, text: str) -> None:
    """Уведомление пользователю о решении; мог заблокировать бота — гасим."""
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        import logging
        logging.getLogger("system").warning(
            f"Access user notify failed for {user_id}: {e}"
        )


async def _edit_card(callback: CallbackQuery, html_text: str) -> None:
    """Перерисовывает карточку запроса (edit_message_text) + подтверждение кнопки."""
    try:
        if callback.message is not None:
            await callback.message.edit_text(html_text, parse_mode="HTML")
    except Exception:
        pass  # карточка могла быть удалена/изменена — не критично
    await safe_answer(callback, "Готово")
