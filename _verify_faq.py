"""Проверка handlers/faq.py («❓ FAQ», поиск) + таблиц faq_* (перенос из data/faq.json).

Запуск: .venv\\Scripts\\python.exe _verify_faq.py
"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import bot  # noqa: E402
import handlers.calculator as calc  # noqa: E402
import handlers.faq as faq  # noqa: E402
from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from db import close_db, init_db  # noqa: E402
from db.engine import init_engine  # noqa: E402
from keyboards import MAIN_BUTTONS, main_kb  # noqa: E402


class FakeMsg:
    """Fake aiogram Message: answer/edit_text пишут в .answers/.edits."""

    def __init__(self, text=None, user_id=4242):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []
        self.edits = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append((text, reply_markup, parse_mode))
        return SimpleNamespace(message_id=len(self.answers))

    async def edit_text(self, text, reply_markup=None, parse_mode=None):
        self.edits.append((text, reply_markup, parse_mode))
        return True


class FakeCb:
    """Fake aiogram CallbackQuery с редактируемым message."""

    def __init__(self, data, user_id=4242):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = FakeMsg()
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))
        return True


def _cb(data):
    return FakeCb(data)


async def main():
    ok = []

    # Изолированная БД в памяти: таблицы + первичный перенос FAQ из JSON
    init_engine("sqlite+aiosqlite://")
    await init_db()

    # --- 0) Роутер и меню -------------------------------------------------
    router_names = [getattr(r, "name", "") for r in bot.dp.sub_routers]
    assert "faq" in router_names, router_names
    # faq обязан стоять до fallback (сравнение по объектам, как в _verify_scenarios)
    from handlers.fallback import router as fallback_router

    subs = bot.dp.sub_routers
    faq_idx = next(i for i, x in enumerate(subs) if x is faq.router)
    fb_idx = next(i for i, x in enumerate(subs) if x is fallback_router)
    assert faq_idx < fb_idx, router_names
    # FAQ открывается из «📚 Шпаргалки» (cheat:faq): сама кнопка — в MAIN_BUTTONS
    assert "📚 Шпаргалки" in MAIN_BUTTONS, "нет кнопки в MAIN_BUTTONS"
    kb_texts = [b.text for row in main_kb.keyboard for b in row]
    assert "📚 Шпаргалки" in kb_texts, kb_texts
    assert "📚 Шпаргалки" in calc.MENU_BUTTONS, "нет зеркала в калькуляторе"
    ok.append("router+menu")

    # --- 1) Данные: таблицы faq_* (перенесены из data/faq.json) -----------
    cats = await faq.get_categories()
    assert len(cats) >= 4, f"слишком мало категорий: {len(cats)}"
    total = sum(len(c["questions"]) for c in cats)
    assert 10 <= total <= 30, f"вопросов {total}, ждём 10–30"
    for c in cats:
        assert c["name"], "категория без name"
        assert c["questions"], f"пустая категория {c['name']}"
        for item in c["questions"]:
            assert item["q"] and item["a"], f"пустой q/a в «{c['name']}»"
    ok.append(f"faq: {len(cats)} категорий, {total} вопросов")

    # --- 2) Поиск: ранжирование и регистр ----------------------------------
    m = faq._find("гарантия", cats)
    assert m and "гарантия" in m[0][2]["q"].lower(), "«гарантия» не нашлась"
    assert faq._find("ГАРАНТИЯ", cats) == m, "регистр должен игнорироваться"
    assert any("мороз" in (it["q"] + it["a"]).lower()
               for _, _, it in faq._find("мороз", cats)), "«мороз» не нашёлся"
    assert any("юрлиц" in (it["q"] + it["a"]).lower()
               for _, _, it in faq._find("юрлиц", cats)), "«юрлица» не нашлись"
    # вопрос ранжируется выше ответа: слово из вопроса — топ результата
    m2 = faq._find("маркировке", cats)
    assert m2 and "S и P" in m2[0][2]["q"], m2[0][2]["q"] if m2 else ""
    assert faq._find("йцукенгшщ", cats) == [], "мусорный запрос не должен находить"
    assert faq._find("аб", cats) == [], "слишком короткое слово игнорируется"
    # много слов: пересечение слов даёт более высокий балл
    m3 = faq._find("гарантия акб", cats)
    assert m3 and "гарантия" in m3[0][2]["q"].lower()
    ok.append("search-scoring")

    # --- 3) Навигация: категории → вопрос → ответ --------------------------
    state = FSMContext(storage=MemoryStorage(),
                       key=StorageKey(bot_id=1, chat_id=4242, user_id=4242))
    fm = FakeMsg("❓ FAQ")
    await faq.button_faq(fm, state)
    assert (await state.get_state()) is None
    text, kb, mode = fm.answers[-1]
    assert mode == "HTML" and "FAQ" in text
    btns = [b for row in kb.inline_keyboard for b in row]
    for c in cats:
        assert any(c["name"] in b.text for b in btns), f"нет кнопки «{c['name']}»"
    assert any(b.callback_data == "faq:search" for b in btns)
    ok.append("main-screen")

    cb = _cb("faq:c:0")
    await faq.callback_category(cb)
    text, kb, mode = cb.message.edits[-1]
    assert mode == "HTML" and cats[0]["name"] in text
    for i, item in enumerate(cats[0]["questions"]):
        assert any(b.callback_data == f"faq:q:0:{i}"
                   for row in kb.inline_keyboard for b in row), f"нет кнопки q:0:{i}"
    ok.append("category-screen")

    cb = _cb("faq:q:0:0")
    await faq.callback_question(cb)
    text, kb, mode = cb.message.edits[-1]
    assert cats[0]["questions"][0]["a"][:40] in text, "ответ не показан"
    assert cats[0]["questions"][0]["q"] in text
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "faq:c:0" in datas and "faq:menu" in datas, datas
    ok.append("answer-screen")

    # назад: ответ → категория → главное
    await faq.callback_menu(_cb("faq:menu"), state)
    assert (await state.get_state()) is None
    ok.append("back-nav")

    print("OK so far:", ", ".join(ok))
    return ok, cats, state


# _cb определён до main; финальные проверки — в части 2 (append ниже)


async def main2(ok, cats, state):
    # --- 4) FSM-поиск: запрос → результаты ---------------------------------
    cb = _cb("faq:search")
    await faq.callback_search(cb, state)
    assert (await state.get_state()) == faq.FaqState.waiting_for_query
    assert "Поиск" in cb.message.edits[-1][0]
    assert any(b.callback_data == "faq:cancel"
               for row in cb.message.edits[-1][1].inline_keyboard for b in row)

    fm = FakeMsg("мороз")
    await faq.process_search_text(fm, state)
    assert (await state.get_state()) is None, "FSM должен быть сброшен"
    text, kb, mode = fm.answers[-1]
    assert mode == "HTML" and "мороз" in text.lower()
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(d.startswith("faq:q:") for d in datas), datas
    # клик по результату поиска открывает ответ
    target = next(d for d in datas if d.startswith("faq:q:"))
    cb2 = _cb(target)
    await faq.callback_question(cb2)
    assert "Категория" in cb2.message.edits[-1][0]
    ok.append("search-fsm")

    # --- 5) Пустой результат ------------------------------------------------
    await faq.callback_search(_cb("faq:search"), state)
    fm = FakeMsg("йцукенгшщз")
    await faq.process_search_text(fm, state)
    assert "Ничего не нашёл" in fm.answers[-1][0]
    ok.append("search-empty")

    # --- 6) Выходы: слово выхода, кнопка меню, повторное «❓ FAQ» ------------
    for word in ("отмена", "💰 Калькулятор", "❓ FAQ"):
        await faq.callback_search(_cb("faq:search"), state)
        assert (await state.get_state()) == faq.FaqState.waiting_for_query
        fm = FakeMsg(word)
        await faq.process_search_text(fm, state)
        assert (await state.get_state()) is None, f"не вышли по «{word}»"
    # кнопка «❌ Отмена» в промпте
    await faq.callback_search(_cb("faq:search"), state)
    await faq.callback_cancel(_cb("faq:cancel"), state)
    assert (await state.get_state()) is None
    ok.append("exits")

    # --- 7) Битые callback-и не роняют поток --------------------------------
    await faq.callback_question(_cb("faq:q:99:99"))
    await faq.callback_question(_cb("faq:q:xx"))
    await faq.callback_category(_cb("faq:c:99"))
    await faq.callback_category(_cb("faq:c:zz"))
    ok.append("bad-callbacks")

    # --- 8) Меню-кнопки FAQ-модуля (для выхода из FSM) -----------------------
    assert "⏰ Напоминания" in faq.MENU_BUTTONS
    assert faq.FAQ_BUTTON not in faq.MENU_BUTTONS
    ok.append("menu-mirror")

    print("\nOK:", ", ".join(ok))
    print(f"Всего проверок: {len(ok)} блоков — все пройдены.")
    await close_db()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ok, cats, state = loop.run_until_complete(main())
    loop.run_until_complete(main2(ok, cats, state))

