"""Временная проверка handlers/calculator.py (FSM MarginState) и его подключения в bot.py.
Запуск: .venv\\Scripts\\python.exe _verify_calc.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import bot  # noqa: E402  (регистрирует все хендлеры, включая include_router калькулятора)
import handlers.calculator as calc  # noqa: E402

# История — во временный файл, чтобы не трогать реальную config/calc_history.json
calc.HISTORY_PATH = Path(tempfile.gettempdir()) / "calc_history_test.json"
if calc.HISTORY_PATH.exists():
    calc.HISTORY_PATH.unlink()

from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402


class FakeUser:
    id = 4242
    username = "tester"


class FakeMsg:
    def __init__(self, text):
        self.text = text
        self.from_user = FakeUser()
        self.answers = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append((text, reply_markup, parse_mode))


class FakeCbMsg:
    def __init__(self):
        self.edits = []
        self.chat = SimpleNamespace(id=4242)

    async def edit_text(self, text, reply_markup=None, parse_mode=None):
        self.edits.append((text, reply_markup, parse_mode))


class FakeCb:
    def __init__(self, data):
        self.data = data
        self.message = FakeCbMsg()
        self.from_user = FakeUser()
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))





async def main():
    ok = []

    # 0) роутер подключён, кнопки в меню
    assert any(getattr(r, "name", "") == "calculator" for r in bot.dp.sub_routers), "router not included"
    assert "💰 Калькулятор" in bot.MAIN_BUTTONS and "📊 История расчётов" in bot.MAIN_BUTTONS
    kb_texts = [b.text for row in bot.main_kb.keyboard for b in row]
    assert "💰 Калькулятор" in kb_texts and "📊 История расчётов" in kb_texts
    ok.append("router+menu")

    # prices.json покрывает все модели каталога
    prices = calc._load_prices()
    assert prices, "prices.json пуст"
    missing = set(bot.MODELS) - set(prices)
    assert not missing, f"нет цен для: {missing}"
    ok.append("prices.json")

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=4242, user_id=4242)
    state = FSMContext(storage=storage, key=key)

    # 1) старт калькулятора
    m = FakeMsg("💰 Калькулятор")
    await calc.start_calculator(m, state)
    assert (await state.get_state()) == calc.MarginState.choosing_model
    labels = [b.text for row in m.answers[-1][1].inline_keyboard for b in row]
    assert any(t.startswith("6S2P") for t in labels) and any("Отмена" in t for t in labels)
    ok.append("start")

    # 2) выбор модели
    await calc.callback_choose_model(FakeCb("calc:model:6S4P"), state)
    assert (await state.get_state()) == calc.MarginState.waiting_for_cost
    assert (await state.get_data())["model"] == "6S4P"
    ok.append("model")

    # 3) закупочная цена текстом (с пробелом-разделителем)
    await calc.process_cost_input(FakeMsg("30 000"), state)
    assert (await state.get_data())["cost"] == 30000.0
    assert (await state.get_state()) == calc.MarginState.waiting_for_price
    ok.append("cost")

    # 4) цена продажи → результат, история, состояние сброшено
    m = FakeMsg("35000")
    await calc.process_price_input(m, state)
    assert (await state.get_state()) is None
    text = m.answers[-1][0]
    assert "5 000 ₽" in text, text
    assert "14.3 %" in text, text
    assert "1 500 ₽" in text, text  # предел скидки = 30% от маржи 5 000
    hist = calc.get_history(4242)
    assert hist and hist[0]["model"] == "6S4P" and hist[0]["margin"] == 5000.0
    ok.append("result+history")

    # 5) базовая цена из config одной кнопкой
    await calc.start_calculator(FakeMsg("💰 Калькулятор"), state)
    await calc.callback_choose_model(FakeCb("calc:model:6S2P"), state)
    await calc.callback_cost_base(FakeCb("calc:cost:base"), state)
    assert (await state.get_data())["cost"] == float(prices["6S2P"])
    assert (await state.get_state()) == calc.MarginState.waiting_for_price
    ok.append("base-price")

    # 6) продажа ниже закупки — предупреждение об убытке
    m = FakeMsg("10 000")
    await calc.process_price_input(m, state)
    assert "убыток" in m.answers[-1][0].lower(), m.answers[-1][0]
    ok.append("loss-advice")

    # 7) выход словом «отмена» и командой /cancel
    await calc.start_calculator(FakeMsg("💰 Калькулятор"), state)
    await calc.process_cost_input(FakeMsg("отмена"), state)
    assert (await state.get_state()) is None
    await calc.start_calculator(FakeMsg("💰 Калькулятор"), state)
    await calc.cmd_cancel(FakeMsg("/cancel"), state)
    assert (await state.get_state()) is None
    ok.append("cancel")

    # 8) лимит истории = 5, свежие первыми
    for i in range(7):
        await calc.add_history_entry(777, {
            "ts": f"2026-09-15T10:0{i}:00", "model": f"M{i}",
            "cost": 1, "price": 2, "margin": 1, "margin_pct": 50.0,
        })
    h = calc.get_history(777)
    assert len(h) == 5 and h[0]["model"] == "M6" and h[-1]["model"] == "M2"
    ok.append("history-limit")

    # 9) рендер истории: пусто и не пусто
    assert "Пока пусто" in calc._render_history([])
    txt = calc._render_history(h)
    assert "M6" in txt and "15.09 10:06" in txt
    ok.append("history-render")

    # 10) парсер сумм
    for raw, expected in [("15000", 15000.0), ("15 000", 15000.0), ("28,5", 28.5),
                          ("28к", 28000.0), ("15 000 руб.", 15000.0), ("abc", None), ("-5", None)]:
        assert calc._parse_money(raw) == expected, raw
    ok.append("money-parser")

    print("OK:", ", ".join(ok))
    if calc.HISTORY_PATH.exists():
        calc.HISTORY_PATH.unlink()


asyncio.run(main())
