"""Админ-команды (пп. 3.1–3.2): /stats, /add_faq, /update_price, /cancel.

Хендлеры вызываются напрямую с фейками (паттерн test_handlers_faq.py);
ADMIN_ID=1 задан в conftest. Не-админ получает тишину — как у /health.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import handlers.admin as admin  # noqa: F401 — проверка импортируемости роутера
from db import calc_history, counters, faq, prices, users
from db.engine import get_sessionmaker
from db.models import User
from handlers.admin import (
    AdminState,
    cmd_add_faq,
    cmd_cancel,
    cmd_stats,
    cmd_update_price,
    faq_answer_not_text,
    faq_category_not_text,
    faq_question_not_text,
    price_code_not_text,
    price_value_not_text,
    process_faq_answer,
    process_faq_category,
    process_faq_question,
    process_price_code,
    process_price_value,
)
from tests.fakes import make_state

ADMIN = SimpleNamespace(id=1, username="boss", first_name="Босс")
NOT_ADMIN = SimpleNamespace(id=42, username="tester", first_name="Тест")


def _msg(text=None, user=NOT_ADMIN) -> SimpleNamespace:
    msg = SimpleNamespace(text=text, from_user=user, answers=[])

    async def answer(t, **kwargs):
        msg.answers.append((t, kwargs))

    msg.answer = answer
    return msg


def _admin_msg(text=None) -> SimpleNamespace:
    return _msg(text, user=ADMIN)


async def _backdate(user_id: int, days: int) -> None:
    async with get_sessionmaker()() as session:
        row = await session.get(User, user_id)
        row.last_seen_at = datetime.now() - timedelta(days=days)
        await session.commit()


# ---------- Доступ: только ADMIN_ID ----------

async def test_commands_silent_for_non_admin():
    state = make_state()
    msg = _msg("/stats")
    await cmd_stats(msg)
    assert msg.answers == []                       # тишина
    await cmd_add_faq(msg, state)
    assert await state.get_state() is None         # FSM не стартовал
    await cmd_update_price(msg, state)
    assert await state.get_state() is None


# ---------- /stats ----------

async def test_stats_reports_all_counters():
    await users.touch(1, "boss")
    await users.touch(5, "seller")
    await _backdate(5, 8)                          # выпал из окна 7 дней
    await counters.incr("ai_ask", 3)
    await counters.incr("ai_voice", 2)
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    baseline = await calc_history.count_since(midnight)
    await calc_history.add(1, {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "model": "6S2P", "cost": 10000, "price": 15000,
        "margin": 5000, "margin_pct": 33.3,
    })
    msg = _admin_msg("/stats")
    await cmd_stats(msg)
    text = msg.answers[0][0]
    assert "Пользователей всего: <b>2</b>" in text
    assert "Активных за 7 дней: <b>1</b>" in text
    assert f"Расчётов маржи сегодня: <b>{baseline + 1}</b>" in text
    assert "Обращений к AI сегодня: <b>5</b>" in text
    assert "текст: 3, голос: 2" in text
    assert "FAQ:" in text


# ---------- /add_faq ----------

async def test_add_faq_into_existing_category_case_insensitive():
    cats = await faq.load()
    target = cats[0]["name"]
    before = len(cats[0]["questions"])
    state = make_state()
    await cmd_add_faq(_admin_msg(), state)
    assert await state.get_state() == AdminState.faq_question
    await process_faq_question(_admin_msg("Как заряжать АКБ?"), state)
    assert await state.get_state() == AdminState.faq_answer
    await process_faq_answer(_admin_msg("Зарядным устройством из комплекта."), state)
    assert await state.get_state() == AdminState.faq_category
    msg = _admin_msg(target.lower())               # регистр не важен
    await process_faq_category(msg, state)
    assert await state.get_state() is None
    assert "существующая категория" in msg.answers[-1][0]
    cats = await faq.load()
    assert cats[0]["questions"][-1]["q"] == "Как заряжать АКБ?"
    assert len(cats[0]["questions"]) == before + 1


async def test_add_faq_new_category_and_html_escaping():
    state = make_state()
    await cmd_add_faq(_admin_msg(), state)
    await process_faq_question(_admin_msg("<b>Что с гарантией?</b>"), state)
    await process_faq_answer(_admin_msg("12 месяцев."), state)
    msg = _admin_msg("Тестовая")
    await process_faq_category(msg, state)
    assert "создана новая категория" in msg.answers[-1][0]
    cats = await faq.load()
    assert cats[-1]["name"] == "Тестовая"          # новая — в конце списка
    q = cats[-1]["questions"][-1]
    assert q["q"] == "&lt;b&gt;Что с гарантией?&lt;/b&gt;"   # HTML безопасен
    assert q["a"] == "12 месяцев."


async def test_add_faq_cancel_and_unknown_command_during_input():
    state = make_state()
    await cmd_add_faq(_admin_msg(), state)
    await process_faq_question(_admin_msg("отмена"), state)
    assert await state.get_state() is None         # слово выхода

    await cmd_add_faq(_admin_msg(), state)
    msg = _admin_msg("/health")
    await process_faq_question(msg, state)         # команда не становится вопросом
    assert await state.get_state() == AdminState.faq_question
    assert "не поддерживаются" in msg.answers[-1][0]


async def test_add_faq_non_text_hint():
    state = make_state()
    await state.set_state(AdminState.faq_answer)
    msg = _msg(None, user=ADMIN)                   # фото и т.п.
    await faq_answer_not_text(msg)
    assert "Жду текст ответа" in msg.answers[0][0]
    await state.set_state(AdminState.faq_category)
    msg = _msg(None, user=ADMIN)
    await faq_category_not_text(msg)
    assert "Жду название категории" in msg.answers[0][0]
    await state.set_state(AdminState.faq_question)
    msg = _msg(None, user=ADMIN)
    await faq_question_not_text(msg)
    assert "Жду текст вопроса" in msg.answers[0][0]


# ---------- /update_price ----------

async def test_update_price_default_field_is_retail():
    code = next(iter(await prices.load()))
    old = await prices.get(code)
    state = make_state()
    await cmd_update_price(_admin_msg(), state)
    assert await state.get_state() == AdminState.price_code
    await process_price_code(_admin_msg(code), state)
    assert await state.get_state() == AdminState.price_value
    msg = _admin_msg("35000")                      # без префикса — розница
    await process_price_value(msg, state)
    assert await state.get_state() is None
    assert "Цена обновлена" in msg.answers[-1][0]
    entry = await prices.get(code)
    assert entry["cash"] == 35000
    assert entry["cost"] == old["cost"]            # прочие ступени не тронуты


async def test_update_price_tier_and_lowercase_code():
    code = next(iter(await prices.load()))
    state = make_state()
    await cmd_update_price(_admin_msg(), state)
    await process_price_code(_admin_msg(code.lower()), state)
    assert await state.get_state() == AdminState.price_value
    msg = _admin_msg("закупка=12345")
    await process_price_value(msg, state)
    assert await state.get_state() is None
    entry = await prices.get(code.upper()) if code.upper() != code \
        else await prices.get(code)
    assert entry["cost"] == 12345


async def test_update_price_unknown_model_keeps_state():
    state = make_state()
    await cmd_update_price(_admin_msg(), state)
    msg = _admin_msg("НЕ-СУЩЕСТВУЕТ")
    await process_price_code(msg, state)
    assert await state.get_state() == AdminState.price_code
    assert "не найдена" in msg.answers[-1][0]
    assert "Доступные коды" in msg.answers[-1][0]


async def test_update_price_unknown_tier_keeps_state():
    code = next(iter(await prices.load()))
    state = make_state()
    await cmd_update_price(_admin_msg(), state)
    await process_price_code(_admin_msg(code), state)
    msg = _admin_msg("вес=5")
    await process_price_value(msg, state)
    assert await state.get_state() == AdminState.price_value   # не съедено
    assert "ступень" in msg.answers[-1][0]


async def test_update_price_non_text_hints():
    state = make_state()
    await state.set_state(AdminState.price_code)
    msg = _msg(None, user=ADMIN)
    await price_code_not_text(msg)
    assert "Жду код модели" in msg.answers[0][0]
    await state.set_state(AdminState.price_value)
    msg = _msg(None, user=ADMIN)
    await price_value_not_text(msg)
    assert "Жду новую цену" in msg.answers[0][0]


# ---------- /cancel ----------

async def test_cancel_command_clears_state():
    state = make_state()
    await state.set_state(AdminState.price_value)
    msg = _admin_msg("/cancel")
    await cmd_cancel(msg, state)
    assert await state.get_state() is None
    assert "Отменено" in msg.answers[0][0]

