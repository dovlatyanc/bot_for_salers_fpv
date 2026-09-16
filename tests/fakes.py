"""Фейковые Message/CallbackQuery/FSM для тестов хендлеров (паттерн _verify_*.py)."""

from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage


class FakeUser:
    id = 42
    username = "tester"


class FakeMsg:
    """Reply-сообщение: ответ бота пишется в self.answers."""

    def __init__(self, text=None):
        self.text = text
        self.from_user = FakeUser()
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class FakeCbMsg:
    """Сообщение внутри CallbackQuery: правки/ответы пишутся в edits/answers."""

    def __init__(self):
        self.from_user = FakeUser()
        self.chat = SimpleNamespace(id=FakeUser.id)
        self.edits = []
        self.answers = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class FakeCb:
    """CallbackQuery: callback.answer() пишется в self.alerts."""

    def __init__(self, data):
        self.data = data
        self.from_user = FakeUser()
        self.message = FakeCbMsg()
        self.alerts = []

    async def answer(self, text=None, show_alert=False):
        self.alerts.append((text, show_alert))


def kb_data(kb) -> list:
    """[(text, callback_data)] по всем кнопкам inline-клавиатуры."""
    return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]


def make_state(bot_id: int = 1, chat_id: int = 42, user_id: int = 42) -> FSMContext:
    """FSMContext на MemoryStorage — как в _verify_scenarios.py."""
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=bot_id, chat_id=chat_id, user_id=user_id),
    )