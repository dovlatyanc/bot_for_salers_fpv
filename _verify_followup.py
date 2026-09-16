"""
Проверка handlers/followup.py (FSM FollowUpState) и его подключения в bot.py:
  1. Роутер «followup» включён в bot.dp, кнопка «⏰ Напоминания» есть в
     MAIN_BUTTONS (иначе текст уйдёт в AI-фолбэк) и в main_kb;
  2. «⏰ Напоминания» в MENU_BUTTONS калькулятора (выход из его FSM);
  3. Парсер срока parse_due: «через N дней/недель/минут», «завтра»,
     «послезавтра», «сегодня», «ДД.ММ[.ГГГГ]», без срока;
  4. Поток: кнопка → пустой список; ➕ → FSM; текст с «через 3 дня» →
     сохранён в data/followups.json (временный) с due/created_at, FSM сброшен;
  5. Просмотр: группы «Предстоящие», кнопка «✅ Выполнить» с id;
  6. Отметка выполненным → пропало из активных, появилось в «Выполненных»;
     вернуть; удалить; изоляция пользователей;
  7. Выходы из FSM: «отмена», кнопка меню, повторная «⏰ Напоминания»;
  8. collect_due — активные напоминания со сроком на дату;
  9. ЭТАП 2 (система напоминаний о follow-up): адресаты daily_recipients,
     рендер render_digest, рассылка send_daily_digest, _run_digest с
     отметкой last_daily (защита от дубля при рестарте).

Запуск: .venv\\Scripts\\python.exe _verify_followup.py
"""
import asyncio
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import bot  # noqa: E402
import handlers.calculator as calc  # noqa: E402  (зеркало MENU_BUTTONS)
import handlers.followup as fup  # noqa: E402
from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from keyboards import MAIN_BUTTONS, main_kb  # noqa: E402

# Хранилище — во временный файл, чтобы не трогать реальный data/followups.json
fup.FOLLOWUPS_PATH = Path(tempfile.gettempdir()) / "followups_test.json"
if fup.FOLLOWUPS_PATH.exists():
    fup.FOLLOWUPS_PATH.unlink()


class FakeMsg:
    def __init__(self, text, user_id=4242):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id, username="tester")
        self.answers = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append((text, reply_markup, parse_mode))


class FakeCb:
    def __init__(self, data, user_id=4242):
        self.data = data
        self.message = SimpleNamespace(edits=[], chat=SimpleNamespace(id=user_id))
        self.from_user = SimpleNamespace(id=user_id, username="tester")
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def _edit_text(cb_msg, text, reply_markup=None, parse_mode=None):
    cb_msg.edits.append((text, reply_markup, parse_mode))


def _cb(data, user_id=4242):
    """FakeCb с рабочим (асинхронным) message.edit_text — как у aiogram."""
    c = FakeCb(data, user_id)

    async def _edit(text, reply_markup=None, parse_mode=None):
        _edit_text(c.message, text, reply_markup, parse_mode)

    c.message.edit_text = _edit
    return c


async def main():
    ok = []

    # 0) роутер и кнопка меню
    assert any(getattr(r, "name", "") == "followup" for r in bot.dp.sub_routers), "router not included"
    assert "⏰ Напоминания" in MAIN_BUTTONS, "нет в MAIN_BUTTONS (уйдёт в AI-фолбэк)"
    kb_texts = [b.text for row in main_kb.keyboard for b in row]
    assert "⏰ Напоминания" in kb_texts, "нет в main_kb"
    assert "⏰ Напоминания" in calc.MENU_BUTTONS, "нет в MENU_BUTTONS калькулятора (выход из FSM)"
    ok.append("router+menu")

    # 1) парсер срока
    today = date(2026, 9, 15)
    cases = [
        ("Клиент @u, модель 6S4P, позвонить через 3 дня", today + timedelta(days=3)),
        ("напомнить через 2 недели", today + timedelta(days=14)),
        ("позвонить завтра", today + timedelta(days=1)),
        ("послезавтра везти", today + timedelta(days=2)),
        ("сегодня созвон", today),
        ("через 30 минут", today),
        ("25.09", date(2026, 9, 25)),
        ("01.03.2027", date(2027, 3, 1)),
        ("без срока", None),
        ("", None),
    ]
    for raw, expected in cases:
        got = fup.parse_due(raw, today)
        assert got == expected, f"parse_due({raw!r}) = {got}, ожидалось {expected}"
    ok.append("parse-due")

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=4242, user_id=4242)
    state = FSMContext(storage=storage, key=key)

    # 2) старт: пустой список, состояние не задето
    m = FakeMsg("⏰ Напоминания")
    await fup.button_followup(m, state)
    assert (await state.get_state()) is None
    assert "Пока пусто" in m.answers[-1][0], m.answers[-1][0]
    ok.append("start-empty")

    # 3) ➕ → FSM → текст с «через 3 дня»
    cb = _cb("fup:new")
    await fup.callback_new(cb, state)
    assert (await state.get_state()) == fup.FollowUpState.waiting_for_text
    m = FakeMsg("Клиент @username, модель 6S4P, позвонить через 3 дня")
    await fup.process_followup_text(m, state)
    assert (await state.get_state()) is None, "FSM не сброшен после сохранения"
    items = fup.get_active(4242)
    assert len(items) == 1, items
    assert items[0]["text"].startswith("Клиент @username")
    assert items[0]["due"] == (date.today() + timedelta(days=3)).isoformat(), items[0]["due"]
    assert items[0]["created_at"] and items[0]["done"] is False and items[0]["done_at"] is None
    raw = fup.FOLLOWUPS_PATH.read_text(encoding="utf-8")
    assert '"seq": 1' in raw and '"user_id": 4242' in raw
    assert "сохранено" in m.answers[-1][0]
    ok.append("create")

    # 4) просмотр: группа «Предстоящие», текст напоминания, кнопка ✅ с id
    m = FakeMsg("⏰ Напоминания")
    await fup.button_followup(m, state)
    text, kb, mode = m.answers[-1]
    assert mode == "HTML"
    assert "Предстоящие" in text and "модель 6S4P" in text, text
    btns = [b for row in kb.inline_keyboard for b in row]
    assert any(b.callback_data == f"fup:done:{items[0]['id']}" for b in btns), [b.callback_data for b in btns]
    ok.append("view")

    # 5) collect_due — хук утренней сводки (ЭТАП 2)
    assert fup.collect_due(4242) == []
    added = await fup.add_followup(4242, "Позвонить сегодня", date.today())
    assert len(fup.collect_due(4242)) == 1
    assert fup.collect_due(4242, date.today() + timedelta(days=1)) == []
    ok.append("collect-due")

    # 6) ✅ выполнено → из активных пропало, в «Выполненных» появилось
    fid = items[0]["id"]
    assert await fup.mark_done(4242, fid)
    assert all(i["id"] != fid for i in fup.get_active(4242))
    done = fup.get_done(4242)
    assert done and done[0]["id"] == fid and done[0]["done_at"], done
    cb = _cb("fup:done_list")
    await fup.callback_done_list(cb)
    assert "Выполненные" in cb.message.edits[-1][0] and "модель 6S4P" in cb.message.edits[-1][0]
    ok.append("mark-done")

    # 7) ↩️ вернуть → активных снова 2 (с «Позвонить сегодня»); 🗑 удалить
    cb = _cb(f"fup:undone:{fid}")
    await fup.callback_undone(cb)
    assert len(fup.get_active(4242)) == 2
    await fup.mark_done(4242, fid)
    cb = _cb(f"fup:del:{fid}")
    await fup.callback_del(cb)
    assert all(i["id"] != fid for i in fup.get_done(4242)), "удалённое осталось в файле"
    ok.append("undone+delete")

    # 8) изоляция пользователей: чужое id не трогаем
    foreign = await fup.add_followup(4242, "чужому не положено", None)
    assert await fup.mark_done(777, foreign["id"]) is False
    assert await fup.reopen(777, foreign["id"]) is False
    assert await fup.delete_followup(777, foreign["id"]) is False
    assert fup.get_active(4242)[0]["text"] == "чужому не положено" or len(fup.get_active(4242)) == 2
    await fup.callback_done(_cb(f"fup:done:{foreign['id']}", user_id=777))
    assert all(i["id"] != foreign["id"] for i in fup.get_done(777)), "чужое напоминание помечено"
    ok.append("user-isolation")

    # 9) выходы из FSM: слово отмены, кнопка меню, повторная «⏰ Напоминания»
    await fup.callback_new(_cb("fup:new"), state)
    await fup.process_followup_text(FakeMsg("отмена"), state)
    assert (await state.get_state()) is None
    await fup.callback_new(_cb("fup:new"), state)
    await fup.process_followup_text(FakeMsg("💰 Калькулятор"), state)
    assert (await state.get_state()) is None
    await fup.callback_new(_cb("fup:new"), state)
    m = FakeMsg("⏰ Напоминания")
    await fup.process_followup_text(m, state)
    assert (await state.get_state()) is None
    assert "Напоминания" in m.answers[-1][0], m.answers[-1][0]
    ok.append("exits")

    # 10) ЭТАП 2: система напоминаний о follow-up — адресаты, рендер, рассылка
    today = date.today()
    await fup.add_followup(4242, "сводка на сегодня", today)
    await fup.add_followup(4242, "сводка просрочка", today - timedelta(days=1))
    await fup.add_followup(4242, "сводка будущее", today + timedelta(days=1))
    await fup.add_followup(4243, "чужое на сегодня", today)
    assert fup.daily_recipients() == [4242, 4243], fup.daily_recipients()
    digest = fup.render_digest(4242)
    assert "Follow-up" in digest and "Просроченные" in digest and "На сегодня" in digest
    assert "сводка просрочка" in digest and "сводка на сегодня" in digest
    assert fup.render_digest(999) is None            # без дедлайнов — не беспокоим

    class _FakeBot:
        def __init__(self):
            self.sends = []

        async def send_message(self, chat_id, text, **kwargs):
            self.sends.append((chat_id, text, kwargs))

    fake_bot = _FakeBot()
    assert await fup.send_daily_digest(fake_bot) == [4242, 4243]
    assert {uid for uid, _, _ in fake_bot.sends} == {4242, 4243}
    assert all(kw.get("parse_mode") == "HTML" for _, _, kw in fake_bot.sends)
    cbs = [b.callback_data for row in fake_bot.sends[0][2]["reply_markup"].inline_keyboard
           for b in row]
    assert "fup:list" in cbs and "fup:cancel" in cbs, cbs
    assert fup._get_last_daily() is None             # факт отмечает только _run_digest
    await fup._run_digest(fake_bot, today)
    assert fup._get_last_daily() == today
    assert fup._digest_due_now(today) is False       # рестарт в тот же день — без дубля
    ok.append("digest")

    print("OK:", ", ".join(ok))

    if fup.FOLLOWUPS_PATH.exists():
        fup.FOLLOWUPS_PATH.unlink()


asyncio.run(main())

