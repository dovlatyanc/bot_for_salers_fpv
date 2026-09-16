"""FSM «💰 Калькулятор»: зеркало меню (регрессия «💬 Возражения»), расчёт, история."""

import pytest

import handlers.calculator as calc
from data import MODELS
from handlers.calculator import (
    CALC_BUTTON, HISTORY_BUTTON, MarginState, _load_prices, _parse_money,
    _price_review, _render_history, _render_result, add_history_entry,
    callback_choose_model, callback_cost_base, cmd_cancel, get_history,
    process_cost_input, process_price_input, start_calculator,
)
from keyboards import MAIN_BUTTONS, main_kb
from tests.fakes import FakeCb, FakeMsg, make_state


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    """История расчётов — во временный файл (реальный config/calc_history.json не трогаем)."""
    path = tmp_path / "calc_history.json"
    monkeypatch.setattr(calc, "HISTORY_PATH", path)
    return path


# ---------- меню и конфиги ----------

def test_menu_mirror_derived_from_main_buttons():
    assert calc.MENU_BUTTONS == frozenset(MAIN_BUTTONS) - {CALC_BUTTON, HISTORY_BUTTON}
    # регрессия P2: новые кнопки меню должны вести к выходу из FSM калькулятора
    for btn in ("🎯 Сценарии", "💬 Возражения", "📊 ТТХ", "🔎 Поиск", "❓ FAQ"):
        assert btn in calc.MENU_BUTTONS, btn
    assert CALC_BUTTON not in calc.MENU_BUTTONS
    assert HISTORY_BUTTON not in calc.MENU_BUTTONS
    kb_texts = [b.text for row in main_kb.keyboard for b in row]
    assert CALC_BUTTON in kb_texts and HISTORY_BUTTON in kb_texts


def test_prices_cover_catalog():
    prices = _load_prices()
    assert prices
    assert not (set(MODELS) - set(prices)), "нет цен для части каталога"
    for code, v in prices.items():
        assert v.get("cost", 0) > 0, code
        if "cash" in v and "profit" in v:
            assert abs(v["profit"] - (v["cash"] - v["cost"])) < 1.0, code


def test_money_parser():
    cases = {
        "15000": 15000.0, "15 000": 15000.0, "28,5": 28.5, "28к": 28000.0,
        "15 000 руб.": 15000.0, "abc": None, "-5": None,
    }
    for raw, expected in cases.items():
        assert _parse_money(raw) == expected, raw


# ---------- основной флоу ----------

async def _start_with_model(state, code="6S4P"):
    await start_calculator(FakeMsg("💰 Калькулятор"), state)
    await callback_choose_model(FakeCb(f"calc:model:{code}"), state)


async def test_full_calculation_flow():
    state = make_state()
    await start_calculator(FakeMsg("💰 Калькулятор"), state)
    assert await state.get_state() == MarginState.choosing_model

    await _start_with_model(state)  # модель уже выбрана — state = waiting_for_cost
    assert await state.get_state() == MarginState.waiting_for_cost

    cost = "30000"  # закупочная менеджера, как в _verify_calc (план по прайсу 9 200)
    await process_cost_input(FakeMsg(cost), state)
    assert await state.get_state() == MarginState.waiting_for_price

    result_msg = FakeMsg("35000")
    await process_price_input(result_msg, state)
    assert await state.get_state() is None

    text = result_msg.answers[-1][0]
    assert "5 000 ₽" in text      # маржа 35000 - 30000
    assert "14.3 %" in text       # маржа в процентах
    assert "1 500 ₽" in text      # предел скидки 30% от маржи
    assert "Сверка с прайсом" in text
    assert "ниже плана на 4 200 ₽" in text       # план по прайсу 9 200, факт 5 000
    assert "выше максимальной ступени" in text  # 35000 > НДС 22% (21 265)

    hist = get_history(42)
    assert hist and hist[0]["model"] == "6S4P" and hist[0]["margin"] == 5000.0


async def test_base_price_from_config_one_button():
    state = make_state()
    await _start_with_model(state, code="6S2P")
    cb = FakeCb("calc:cost:base")
    await callback_cost_base(cb, state)
    assert (await state.get_data())["cost"] == _load_prices()["6S2P"]["cost"]
    assert await state.get_state() == MarginState.waiting_for_price


async def test_menu_button_exits_during_input():
    """Регрессия P2: «💬 Возражения» раньше не было в зеркале меню
    и парсилась как число вместо чистого выхода из FSM."""
    state = make_state()
    await _start_with_model(state)
    await process_cost_input(FakeMsg("💬 Возражения"), state)
    assert await state.get_state() is None


async def test_cancel_word_and_command():
    state = make_state()
    await start_calculator(FakeMsg("💰 Калькулятор"), state)
    await process_cost_input(FakeMsg("отмена"), state)
    assert await state.get_state() is None

    await start_calculator(FakeMsg("💰 Калькулятор"), state)
    await cmd_cancel(FakeMsg("/cancel"), state)
    assert await state.get_state() is None


async def test_loss_warning_when_price_below_cost():
    state = make_state()
    await _start_with_model(state, code="6S2P")
    await process_cost_input(FakeMsg("4800"), state)
    msg = FakeMsg("4000")  # продажа ниже закупки
    await process_price_input(msg, state)
    assert "убыток" in msg.answers[-1][0].lower()
    assert "ниже минимальной ступени" in msg.answers[-1][0]


# ---------- история и чистые функции ----------

async def test_history_limit_and_render():
    for i in range(7):
        await add_history_entry(777, {
            "ts": f"2026-09-15T10:0{i}:00", "model": f"M{i}",
            "cost": 1, "price": 2, "margin": 1, "margin_pct": 50.0,
        })
    h = get_history(777)
    assert len(h) == 5 and h[0]["model"] == "M6" and h[-1]["model"] == "M2"
    assert "Пока пусто" in _render_history([])
    rendered = _render_history(h)
    assert "M6" in rendered and "15.09 10:06" in rendered


def test_price_review_and_result_render_without_prices():
    assert _price_review(100.0, 50.0, None) == ""
    entry = {"model": "X", "cost": 100.0, "price": 150.0, "margin": 50.0, "margin_pct": 33.3}
    plain = _render_result(entry)
    assert "Сверка" not in plain and "33.3 %" in plain