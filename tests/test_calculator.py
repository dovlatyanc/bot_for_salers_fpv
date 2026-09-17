"""FSM «💰 Калькулятор»: зеркало меню (регрессия «💬 Возражения»), расчёт, история."""

import handlers.calculator as calc
from data import MODELS
from handlers.calculator import (
    CALC_BUTTON, HISTORY_BUTTON, MarginState, _parse_money,
    _price_review, _render_history, _render_result, add_history_entry,
    callback_choose_model, callback_cost_base, cmd_cancel, get_history,
    get_prices, process_cost_input, process_price_input, start_calculator,
)
from keyboards import MENU_EXIT_BUTTONS, main_kb
from tests.fakes import FakeCb, FakeMsg, make_state


# ---------- меню и конфиги ----------

def test_menu_mirror_derived_from_main_buttons():
    assert calc.MENU_BUTTONS == frozenset(MENU_EXIT_BUTTONS) - {CALC_BUTTON, HISTORY_BUTTON}
    # регрессия P2: кнопки меню (и легаси «📊 ТТХ» с закэшированных клавиатур)
    # должны вести к выходу из FSM калькулятора
    for btn in ("📚 Шпаргалки", "📊 ТТХ", "🔎 Поиск", "🤖 AI-ассистент"):
        assert btn in calc.MENU_BUTTONS, btn
    assert CALC_BUTTON not in calc.MENU_BUTTONS
    assert HISTORY_BUTTON not in calc.MENU_BUTTONS
    kb_texts = [b.text for row in main_kb.keyboard for b in row]
    assert CALC_BUTTON in kb_texts and HISTORY_BUTTON in kb_texts


async def test_prices_cover_catalog():
    prices = await get_prices()
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

    hist = await get_history(42)
    assert hist and hist[0]["model"] == "6S4P" and hist[0]["margin"] == 5000.0


async def test_base_price_from_config_one_button():
    state = make_state()
    await _start_with_model(state, code="6S2P")
    cb = FakeCb("calc:cost:base")
    await callback_cost_base(cb, state)
    assert (await state.get_data())["cost"] == (await get_prices())["6S2P"]["cost"]
    assert await state.get_state() == MarginState.waiting_for_price


async def test_menu_button_exits_during_input():
    """Регрессия P2: кнопка меню, отсутствующая в зеркале (раньше «💬 Возражения»,
    теперь «📚 Шпаргалки»), не должна парситься как число — только чистый выход."""
    state = make_state()
    await _start_with_model(state)
    await process_cost_input(FakeMsg("📚 Шпаргалки"), state)
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
    h = await get_history(777)
    assert len(h) == 5 and h[0]["model"] == "M6" and h[-1]["model"] == "M2"
    assert "Пока пусто" in _render_history([])
    rendered = _render_history(h)
    assert "M6" in rendered and "15.09 10:06" in rendered


def test_price_review_and_result_render_without_prices():
    assert _price_review(100.0, 50.0, None) == ""
    entry = {"model": "X", "cost": 100.0, "price": 150.0, "margin": 50.0, "margin_pct": 33.3}
    plain = _render_result(entry)
    assert "Сверка" not in plain and "33.3 %" in plain


# ---------- скидка vs прайсовая лесенка (регрессия «ниже розницы») ----------

def test_discount_advice_respects_price_floor():
    """FIX: предел скидки не опускает цену ниже минимальной ступени прайса,
    а цена ниже ступени — запрет продажи, а не совет «торговаться»."""
    floor = ("розница", 15500.0)
    # цена ниже «розницы» — раньше советовал скидку 2 300 ₽ и «торговаться»
    below = calc._discount_advice(6300.0, 14000.0, 7700.0, 55.0, floor)
    assert "нельзя" in below and "торговаться" not in below, below
    assert "15 500 ₽" in below, below
    # запас до ступени (100 ₽) жёстче 30% маржи (2 700 ₽) — лимитирует ступень
    capped = calc._discount_advice(6300.0, 15600.0, 9300.0, 59.6, floor)
    assert "100 ₽" in capped and "15 500 ₽" in capped, capped
    assert "торговаться" not in capped, capped
    # цена на ступени — без скидки
    at_floor = calc._discount_advice(6300.0, 15500.0, 9200.0, 59.4, floor)
    assert "без скидки" in at_floor, at_floor
    # без прайса: сумма та же (2 300 ₽), но 2 300 < 2 500 — без «смело торговаться»
    plain = calc._discount_advice(6300.0, 14000.0, 7700.0, 55.0)
    assert "2 300 ₽" in plain and "торговаться" not in plain, plain


def test_bold_trade_phrase_needs_real_headroom():
    """FIX: «Запас по марже большой — можно смело торговаться» — только при
    безопасной скидке от 2 500 ₽ (решение владельца: ступень «НДС 5%»
    триггером фразы больше не является)."""
    # регрессия 6S2P: маржинальность 40 %, но запас всего 100 ₽
    small = calc._discount_advice(4800.0, 8000.0, 3200.0, 40.0, ("розница", 7900.0))
    assert "торговаться" not in small and "Маржа достаточная" in small, small
    assert "100 ₽" in small and "7 900 ₽" in small, small
    # скидка 4 100 ₽ (30% маржи) при запасе 4 500 ₽ — фраза возвращается
    bold = calc._discount_advice(6300.0, 20000.0, 13700.0, 68.5, ("розница", 15500.0))
    assert "можно смело торговаться" in bold and "4 100 ₽" in bold, bold
    # цена на ступени «НДС 5%», но безопасная скидка 2 000 ₽ (< 2 500 ₽) —
    # фразы нет, лимитирует розница; формулировка «Маржа достаточная»
    capped_vat = calc._discount_advice(6300.0, 17500.0, 11200.0, 64.0, ("розница", 15500.0))
    assert "торговаться" not in capped_vat and "Маржа достаточная" in capped_vat, capped_vat
    assert "2 000 ₽" in capped_vat and "15 500 ₽" in capped_vat, capped_vat


def test_render_result_caps_discount_by_ladder():
    """FIX: на экране результата совет по скидке согласован со «Сверкой с прайсом»."""
    entry = {"model": "6S4P", "cost": 6300.0, "price": 14000.0,
             "margin": 7700.0, "margin_pct": 55.0}
    text = _render_result(entry, {"cost": 6300.0, "cash": 15500.0, "profit": 9200.0})
    assert "торговаться" not in text, text
    assert "нельзя" in text, text
    assert "ниже минимальной ступени" in text  # блок сверки по-прежнему показывает позицию

    ok_entry = {"model": "6S4P", "cost": 6300.0, "price": 15600.0,
                "margin": 9300.0, "margin_pct": 59.6}
    text = _render_result(ok_entry, {"cost": 6300.0, "cash": 15500.0, "profit": 9200.0})
    assert "100 ₽" in text and "15 500 ₽" in text, text

    # регрессия 6S2P: маржа 40 %, но запас сверх розницы всего 100 ₽ —
    # фраза «можно смело торговаться» не положена, сумма при этом честная
    s2p = {"model": "6S2P", "cost": 4800.0, "price": 8000.0,
           "margin": 3200.0, "margin_pct": 40.0}
    text = _render_result(
        s2p, {"cost": 4800.0, "cash": 7900.0, "wholesale": 8858.0, "profit": 3100.0},
    )
    assert "торговаться" not in text and "Маржа достаточная" in text, text
    assert "100 ₽" in text and "7 900 ₽" in text, text