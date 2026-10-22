"""db.fsm_storage: персистентное FSM-хранилище aiogram вместо MemoryStorage.

Сценарий аудита п. 1.1: watchdog перезапускает бота — активные FSM-состояния
(«💰 Калькулятор», «🔧 Своя сборка», follow-up) должны пережить рестарт.
"""

import datetime as dt

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey

from db.fsm_storage import SQLAlchemyStorage


def make_key(user_id: int = 42, chat_id: int = 42, destiny: str = "default") -> StorageKey:
    return StorageKey(bot_id=1, chat_id=chat_id, user_id=user_id, destiny=destiny)


class CalcState(StatesGroup):
    waiting_cost = State()
    waiting_price = State()


async def test_state_roundtrip():
    st = SQLAlchemyStorage()
    key = make_key()
    assert await st.get_state(key) is None
    await st.set_state(key, CalcState.waiting_cost)
    # State — подкласс str: в БД лежит его строковое имя
    assert await st.get_state(key) == CalcState.waiting_cost
    await st.set_state(key, "Custom:state")
    assert await st.get_state(key) == "Custom:state"
    await st.set_state(key, None)          # сброс состояния
    assert await st.get_state(key) is None


async def test_data_roundtrip_and_unicode():
    st = SQLAlchemyStorage()
    key = make_key()
    assert await st.get_data(key) == {}
    payload = {"model": "6S4P «Альфа»", "cost": 35000.5, "n": 3}
    await st.set_data(key, payload)
    assert await st.get_data(key) == payload
    await st.set_data(key, {})             # очистка данных
    assert await st.get_data(key) == {}


async def test_state_and_data_independent():
    """set_data не затирает state, set_state не затирает data (upsert по своей колонке)."""
    st = SQLAlchemyStorage()
    key = make_key()
    await st.set_state(key, CalcState.waiting_price)
    await st.set_data(key, {"cost": 100})
    await st.set_data(key, {"cost": 100, "price": 150})
    assert await st.get_state(key) == CalcState.waiting_price
    await st.set_state(key, None)
    assert await st.get_data(key) == {"cost": 100, "price": 150}


async def test_keys_isolated():
    st = SQLAlchemyStorage()
    await st.set_state(make_key(user_id=1), "state:a")
    await st.set_state(make_key(user_id=2), "state:b")
    await st.set_data(make_key(chat_id=100), {"x": 1})
    assert await st.get_state(make_key(user_id=1)) == "state:a"
    assert await st.get_state(make_key(user_id=2)) == "state:b"
    assert await st.get_data(make_key(chat_id=42)) == {}


async def test_destiny_isolated():
    st = SQLAlchemyStorage()
    await st.set_state(make_key(destiny="default"), "state:a")
    await st.set_state(make_key(destiny="other"), "state:b")
    assert await st.get_state(make_key(destiny="default")) == "state:a"
    assert await st.get_state(make_key(destiny="other")) == "state:b"


async def test_survives_engine_restart(tmp_path):
    """Рестарт бота: движок закрыт и создан заново на тот же файл — состояние на месте."""
    import db

    url = f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"
    await SQLAlchemyStorage().set_state(make_key(), CalcState.waiting_price)
    await SQLAlchemyStorage().set_data(make_key(), {"cost": 35000})
    await db.close_db()                    # «остановка бота»
    db.init_engine(url)                    # «новый запуск»: тот же файл БД
    await db.init_db(sources=None)
    fresh = SQLAlchemyStorage()
    assert await fresh.get_state(make_key()) == CalcState.waiting_price
    assert await fresh.get_data(make_key()) == {"cost": 35000}


async def test_close_is_noop():
    """Движком управляет db.close_db() — storage.close() ничего не ломает."""
    await SQLAlchemyStorage().close()


def test_load_tolerates_bad_json():
    assert SQLAlchemyStorage._load(None) == {}
    assert SQLAlchemyStorage._load("") == {}
    assert SQLAlchemyStorage._load("{not json") == {}
    assert SQLAlchemyStorage._load("[1, 2]") == {}   # не словарь


def test_dump_falls_back_to_str():
    raw = SQLAlchemyStorage._dump({"ts": dt.datetime(2026, 1, 1, 12, 0)})
    assert "2026-01-01 12:00:00" in raw     # str(datetime): несериализуемое — строкой, не падение

def test_pk_sentinels():
    key = make_key()
    assert SQLAlchemyStorage._pk(key) == (1, 42, 42, 0, "", "default")
