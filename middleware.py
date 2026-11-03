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

from config import ADMIN_ID
from db import allowed as allowed_db
from db import users as users_db
# Импорт на уровне модуля безопасен: keyboards.access зависит только от
# aiogram (не от middleware/handlers/db), поэтому цикла нет. Локальный
# импорт внутри метода не нужен — направленность зависимостей соблюдена:
#   config ← db/keyboards ← middleware ← bot.py (handlers НЕ импортируются).
from keyboards.access import kb_access_decision, text_access_request

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


# ============================================================
# Контроль доступа: белый список allowed_users (подтверждение админом)
# ============================================================
class AccessControlMiddleware(BaseMiddleware):
    """Пропускает дальше только админа и одобренных (allowed_users.status='approved').

    FAIL-CLOSED по соображениям безопасности; осознанно отличается от fail-open
    в UserTrackingMiddleware: сбой БД здесь означает «доступ не проверить» →
    в хендлеры никого (кроме админа) не пускаем, пользователь получает
    «Сервис временно недоступен». ADMIN_ID пропускается ВСЕГДА, даже при
    упавшей БД — иначе в момент инцидента потеряем /stats и /health.

    Регистрация (bot.py, ПОСЛЕ UserTrackingMiddleware, до include_router):
        dp.message.outer_middleware(AccessControlMiddleware())
        dp.callback_query.outer_middleware(AccessControlMiddleware())

    Пропуск без проверки: события без from_user и от ботов (технические).

    Логика для остальных (fail-closed: любая ошибка БД → «Сервис временно
    недоступен», событие до хендлеров НЕ доходит):
      * status == "approved" → пропустить;
      * status == "pending"  → «Запрос уже отправлен, ожидайте решения»,
        админа НЕ уведомлять повторно (спам-защита), обработку остановить;
      * status == "declined" → нейтральное «У вас нет доступа» без деталей,
        админа не уведомлять, обработку остановить;
      * статуса нет (новый)  → upsert_pending + уведомление админу (ID,
        @username, full_name + кнопки «✅ Одобрить»/«❌ Отклонить») + ответ
        «Запрос отправлен администратору», обработку остановить.

    Исключения по тексту сообщения (до проверок статуса, чтобы не застрять):
      * /start — обрабатывается ВЕТКАМИ middleware (pending/declined/new),
        но в хендлер start из menu.py НЕ доходит: иначе пользователь получил бы
        двойной ответ (ветка middleware + главное меню);
      * /cancel — пропускается ВСЕГДА (универсальная отмена FSM).

    Для callback_query ответ всегда завершается await callback.answer(...)
    (Telegram снимает «часики» на кнопке); инлайн-сообщение при остановке
    обработки не меняется.
    """

    # Тексты для неавторизованных (не раскрывают ни разделов, ни деталей решения)
    MSG_UNAVAILABLE = "⏳ Сервис временно недоступен, попробуйте позже."
    MSG_PENDING = "⏳ Запрос уже отправлен, ожидайте решения администратора."
    MSG_DECLINED = "⛔ У вас нет доступа."
    MSG_SENT = "📨 Запрос отправлен администратору. Ожидайте решения."

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None or getattr(user, "is_bot", False):
            return await handler(event, data)
        if user.id == ADMIN_ID:
            return await handler(event, data)   # админ — всегда, даже при сбое БД

        # /cancel — универсальная отмена FSM: доступна всем (не даём застрять)
        text = getattr(event, "text", None) or ""
        if text.startswith("/cancel"):
            return await handler(event, data)

        try:
            status = await allowed_db.get_status(user.id)
        except Exception as e:
            # FAIL-CLOSED: не смогли проверить доступ — не пускаем (см. докстринг)
            system_logger.error(f"Access check failed for {user.id} (fail-closed): {e}")
            await self._notify(event, self.MSG_UNAVAILABLE)
            return None

        if status == "approved":
            return await handler(event, data)
        if status == "pending":
            await self._notify(event, self.MSG_PENDING)
            return None                      # обработку остановить
        if status == "declined":
            await self._notify(event, self.MSG_DECLINED)
            return None

        # Статуса нет — новый пользователь: запрос в БД + уведомление админу
        try:
            await allowed_db.upsert_pending(
                user_id=user.id,
                username=user.username,
                full_name=" ".join(filter(None, (user.first_name, user.last_name))),
            )
        except Exception as e:
            system_logger.error(f"Access request save failed for {user.id}: {e}")
            await self._notify(event, self.MSG_UNAVAILABLE)
            return None
        await self._notify_admin_request(event)
        await self._notify(event, self.MSG_SENT)
        return None

    async def _notify(self, event, text: str) -> None:
        """Ответ пользователю: message.answer / callback.answer (show_alert)."""
        try:
            if getattr(event, "message", None) is not None:   # callback_query
                await event.answer(text, show_alert=True)
            else:                                             # message
                await event.answer(text)
        except Exception as e:
            system_logger.warning(f"Access notify failed: {e}")

    async def _notify_admin_request(self, event) -> None:
        """Уведомление админу о новом запросе доступа (+ кнопки решения).

        Текст намеренно НЕ раскрывает разделы/данные бота. Ошибка отправки
        гасится: запрос уже в БД, решение можно выдать командой /allow
        (handlers/admin.py). UI-хелперы импортируются на уровне модуля
        (kb_access_decision / text_access_request из keyboards.access) —
        keyboards — нижний слой, middleware → handlers запрещено.
        """
        user = event.from_user
        try:
            await event.bot.send_message(
                ADMIN_ID,
                text_access_request(user),
                reply_markup=kb_access_decision(user.id),
            )
        except Exception as e:
            system_logger.warning(f"Access admin notify failed for {user.id}: {e}")