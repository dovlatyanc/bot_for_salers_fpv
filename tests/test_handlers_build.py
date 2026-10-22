"""«🔧 Своя сборка»: ввод 14S12P и умный разбор параметров (12В / 12000мА·ч / 90А)."""

import handlers.build as build
from handlers.build import (
    CELL_CAP_MAH, CELL_CONT_A, CELL_NOM_V, MAX_P, MAX_S,
    _parse_build_input, process_build, process_build_connector,
    process_build_not_text, start_build,
)
from states import BuildState
from tests.fakes import FakeMsg, make_state


# ---------- парсер ввода ----------

def test_parse_full_config_variants():
    for raw in ("14S12P", "14s12p", "14с12р", "14 s 12 p", " 14S12P "):
        parsed = _parse_build_input(raw)
        assert (parsed["s"], parsed["p"]) == (14, 12), raw
        assert parsed["v"] is None and parsed["cap_mah"] is None and parsed["amps"] is None


def test_parse_voltage_and_capacity():
    assert _parse_build_input("12в 12000мач") == {
        "s": None, "p": None, "v": 12.0, "cap_mah": 12000, "amps": None,
    }
    assert _parse_build_input("12V 12000mAh")["v"] == 12.0
    assert _parse_build_input("22,2В 25000 мА·ч")["v"] == 22.2
    assert _parse_build_input("22.2В 25000 мА·ч")["cap_mah"] == 25000
    assert _parse_build_input("12 вольт 12ач")["cap_mah"] == 12000   # А·ч → мА·ч
    assert _parse_build_input("12В 12Ач")["cap_mah"] == 12000


def test_parse_current_and_unit_conflicts():
    assert _parse_build_input("90а")["amps"] == 90.0
    assert _parse_build_input("90 А")["amps"] == 90.0
    assert _parse_build_input("12амп")["amps"] == 12.0
    assert _parse_build_input("12ампер")["amps"] == 12.0
    # «ач»/«ah» — ёмкость, а не ток
    assert _parse_build_input("12ач")["amps"] is None
    assert _parse_build_input("12ah")["amps"] is None
    assert _parse_build_input("12ah")["cap_mah"] == 12000


def test_parse_single_params_and_garbage():
    assert _parse_build_input("8s")["s"] == 8
    assert _parse_build_input("5P")["p"] == 5
    assert _parse_build_input("12в")["v"] == 12.0
    assert _parse_build_input("12000мА·ч")["cap_mah"] == 12000
    for raw in ("привет", "12", "-", " ", ""):
        assert _parse_build_input(raw) is None, raw


# ---------- FSM: готовая геометрия (старое поведение) ----------

async def _start(state):
    await start_build(FakeMsg("🔧 Своя сборка"), state)
    assert await state.get_state() == BuildState.waiting_for_config


async def test_full_config_flow_unchanged():
    state = make_state()
    await _start(state)
    msg = FakeMsg("14S12P")
    await process_build(msg, state)
    assert await state.get_state() == BuildState.waiting_for_connector
    data = await state.get_data()
    assert data["s"] == 14 and data["p"] == 12
    assert data["nom_v"] == 14 * CELL_NOM_V and data["cap_mah"] == 12 * CELL_CAP_MAH
    texts = " | ".join(t for t, _ in msg.answers)
    assert "Выберите разъём для 14S12P" in texts
    kb = msg.answers[-1][1]["reply_markup"]
    assert any(b.text == "XT90-S" for row in kb.inline_keyboard for b in row)
    assert "Как понял запрос" not in texts  # без пересчётов — без пояснений


async def test_garbage_shows_hint_and_stays():
    state = make_state()
    await _start(state)
    msg = FakeMsg("привет")
    await process_build(msg, state)
    assert "Форматы" in msg.answers[0][0]
    assert await state.get_state() == BuildState.waiting_for_config


async def test_exit_word_still_closes():
    state = make_state()
    await _start(state)
    await process_build(FakeMsg("12в"), state)  # частичный ввод — pending
    msg = FakeMsg("отмена")
    await process_build(msg, state)
    assert await state.get_state() is None
    assert "Своя сборка закрыта" in msg.answers[0][0]


async def test_not_text_hint_mentions_formats():
    msg = FakeMsg(None)
    await process_build_not_text(msg)
    text = msg.answers[0][0]
    assert "14S12P" in text and "12В 12000мА·ч" in text


# ---------- FSM: параметры → S/P ----------

async def test_voltage_and_capacity_derivation():
    state = make_state()
    await _start(state)
    msg = FakeMsg("12в 12000мач")
    await process_build(msg, state)
    assert await state.get_state() == BuildState.waiting_for_connector
    data = await state.get_data()
    assert (data["s"], data["p"]) == (3, 3)
    assert data["cap_mah"] == 12000 and round(data["nom_v"], 1) == 11.1
    text = msg.answers[0][0]
    assert "12 В ≈ 3S" in text and "12000 мА·ч ≈ 3P" in text


async def test_voltage_only_then_capacity():
    state = make_state()
    await _start(state)
    msg1 = FakeMsg("12в")
    await process_build(msg1, state)
    assert await state.get_state() == BuildState.waiting_for_config
    assert "12 В ≈ 3S" in msg1.answers[0][0]
    assert "ёмкость или ток" in msg1.answers[0][0]
    msg2 = FakeMsg("12000mah")
    await process_build(msg2, state)
    assert await state.get_state() == BuildState.waiting_for_connector
    data = await state.get_data()
    assert (data["s"], data["p"]) == (3, 3)
    text = msg2.answers[0][0]
    assert "12 В ≈ 3S" in text and "12000 мА·ч ≈ 3P" in text


async def test_capacity_only_then_voltage():
    state = make_state()
    await _start(state)
    msg1 = FakeMsg("25000мач")
    await process_build(msg1, state)
    assert await state.get_state() == BuildState.waiting_for_config
    assert "25000 мА·ч ≈ 7P" in msg1.answers[0][0]
    assert "напряжение" in msg1.answers[0][0]
    msg2 = FakeMsg("22,2В")
    await process_build(msg2, state)
    data = await state.get_data()
    assert (data["s"], data["p"]) == (6, 7)   # ceil(25000/4000) = 7


async def test_current_only_then_voltage():
    state = make_state()
    await _start(state)
    msg1 = FakeMsg("90А")
    await process_build(msg1, state)
    assert await state.get_state() == BuildState.waiting_for_config
    assert f"90 А ≈ 3P (длительно {CELL_CONT_A} А" in msg1.answers[0][0]
    msg2 = FakeMsg("24в")
    await process_build(msg2, state)
    data = await state.get_data()
    assert (data["s"], data["p"]) == (6, 3)   # ceil(90/35) = 3, round(24/3.7) = 6


async def test_capacity_and_current_take_max():
    state = make_state()
    await _start(state)
    msg = FakeMsg("24в 10000мач 90А")
    await process_build(msg, state)
    data = await state.get_data()
    assert (data["s"], data["p"]) == (6, 3)   # round(24/3.7)=6; max(ceil(10000/4000), ceil(90/35)) = 3
    text = msg.answers[0][0]
    assert "10000 мА·ч ≈ 3P" in text and "90 А ≈ 3P" in text


async def test_explicit_config_overrides_pending():
    state = make_state()
    await _start(state)
    await process_build(FakeMsg("12в"), state)
    msg = FakeMsg("6s2p")
    await process_build(msg, state)
    data = await state.get_data()
    assert (data["s"], data["p"]) == (6, 2)
    assert await state.get_state() == BuildState.waiting_for_connector


async def test_voltage_out_of_range():
    state = make_state()
    await _start(state)
    msg = FakeMsg("100в 12000мач")
    await process_build(msg, state)
    assert "2–60 В" in msg.answers[0][0]
    assert await state.get_state() == BuildState.waiting_for_config


async def test_p_out_of_range_after_both_known():
    state = make_state()
    await _start(state)
    await process_build(FakeMsg("300000мач"), state)  # 75P — запомнено как pending
    msg = FakeMsg("12в")
    await process_build(msg, state)
    text = msg.answers[0][0]
    assert "не похоже на реальную сборку" in text
    assert f"1–{MAX_S}S" in text and f"1–{MAX_P}P" in text
    assert await state.get_state() == BuildState.waiting_for_config


# ---------- итоговая карточка ----------

async def test_end_to_end_result_card():
    state = make_state()
    await _start(state)
    await process_build(FakeMsg("12В 12000мА·ч"), state)
    msg = FakeMsg("ок")   # 3S ≤ 6 → разъём по умолчанию XT60
    await process_build_connector(msg, state)
    assert await state.get_state() is None
    text = msg.answers[-1][0]
    assert "Расчёт для 3S3P" in text
    assert "Ёмкость: 12000 мА·ч (12 А·ч)" in text
    assert "XT60" in text and "Балансир: 4-pin" in text
    assert f"Ток длительно: {CELL_CONT_A * 3} А" in text and "Ток пиково: 135 А" in text
