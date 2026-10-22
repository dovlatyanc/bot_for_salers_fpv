"""FSM-хранилище aiogram поверх SQLAlchemy: таблица user_states (аудит п. 1.1).

Замена MemoryStorage: состояние диалога переживает рестарт бота (watchdog
перезапускает сервис при зависании или по своему таймеру) — менеджер,
заполнявший «💰 Калькулятор» или «🔧 Свою сборку», продолжает с того же шага.

Таблица описана в db.models.UserState: составной PK = StorageKey
(bot_id, chat_id, user_id, thread_id, business_connection_id, destiny;
для отсутствующих thread_id/business_connection_id — сентинелы 0/"", т.к.
PK не может содержать NULL в PostgreSQL). state — имя состояния или NULL,
data — JSON-словарь FSMContext или NULL. Схема диалектно-нейтральная:
SQLite (aiosqlite) сейчас, PostgreSQL (asyncpg) позже — только DB_URL.

Записи — атомарный INSERT ... ON CONFLICT DO UPDATE (upsert) строго по
«своей» колонке: set_state трогает только state, set_data — только data,
поэтому установка состояния не затирает данные и наоборот. Жизненным
циклом движка storage не управляет (init_db/close_db в bot.py): close()
— no-op, Dispatcher можно создавать до init_db(), как и раньше.
"""

import json
import logging
from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from db.engine import get_sessionmaker
from db.models import UserState
from db.retry import retry_on_db_lock

system_logger = logging.getLogger("system")

# Порядок PK-колонок UserState — тот же, в котором возвращает _pk()
_PK_COLUMNS = (
    "bot_id", "chat_id", "user_id", "thread_id", "business_connection_id", "destiny",
)


def _state_str(state: State | str | None) -> str | None:
    """State → его каноничное имя (State.state), str — как есть, None → None.

    Важно: у aiogram State.__str__() возвращает "<State '...'>" — красиво
    для логов, но не годится для хранения; MemoryStorage/RedisStorage тоже
    сохраняют именно state.state.
    """
    if state is None:
        return None
    return state.state if isinstance(state, State) else state


class SQLAlchemyStorage(BaseStorage):
    """Персистентное FSM-хранилище в таблице user_states (СУБД — из DB_URL)."""

    @retry_on_db_lock()
    async def set_state(self, key: StorageKey, state: State | str | None = None) -> None:
        await self._upsert(key, {"state": _state_str(state)})

    @retry_on_db_lock()
    async def get_state(self, key: StorageKey) -> str | None:
        async with get_sessionmaker()() as session:
            row = await session.get(UserState, self._pk(key))
            return row.state if row else None

    @retry_on_db_lock()
    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._upsert(key, {"data": self._dump(dict(data))})

    @retry_on_db_lock()
    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with get_sessionmaker()() as session:
            row = await session.get(UserState, self._pk(key))
            return self._load(row.data if row else None)

    async def close(self) -> None:
        """Движок закрывается централизованно: db.close_db() в bot.py."""

    # ---------- служебные ----------

    @staticmethod
    def _pk(key: StorageKey) -> tuple[int | str, ...]:
        """StorageKey → кортеж PK в порядке колонок модели (сентинелы вместо NULL)."""
        return (
            key.bot_id,
            key.chat_id,
            key.user_id,
            key.thread_id or 0,                      # 0 = «не в topic»
            key.business_connection_id or "",        # "" = обычный чат
            key.destiny or "default",
        )

    @staticmethod
    def _dump(data: dict[str, Any]) -> str:
        """dict → JSON. default=str: случайная несериализуемая величина
        (например, datetime) не должна ронять диалог — сохранится строкой."""
        return json.dumps(data, ensure_ascii=False, default=str)

    @staticmethod
    def _load(raw: str | None) -> dict[str, Any]:
        """JSON → dict; пусто/повреждено/не словарь — пустой dict (как в MemoryStorage)."""
        if not raw:
            return {}
        try:
            loaded = json.loads(raw)
        except (TypeError, ValueError):
            system_logger.warning("FSM data повреждён (не JSON) — трактуем как пустой")
            return {}
        return loaded if isinstance(loaded, dict) else {}

    async def _upsert(self, key: StorageKey, columns: dict[str, str]) -> None:
        """INSERT ... ON CONFLICT DO UPDATE только по переданным колонкам.

        Гонку двух задач на одном ключе решает СУБД: конфликт PK превращается
        в UPDATE (возможен и на PostgreSQL — вставка диалект-специфична).
        """
        values = dict(zip(_PK_COLUMNS, self._pk(key))) | columns
        async with get_sessionmaker()() as session:
            conn = await session.connection()
            insert = sqlite_insert if conn.dialect.name == "sqlite" else pg_insert
            stmt = insert(UserState).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[getattr(UserState, name) for name in _PK_COLUMNS],
                set_={name: getattr(stmt.excluded, name) for name in columns},
            )
            await session.execute(stmt)
            await session.commit()
