"""
Проверка модуля «🎯 Сценарии» (data/scenarios.py + handlers/scenarios.py).

Что проверяется:
  1. Данные: 5–7 сценариев; все recommend-коды существуют в MODELS; qa/extras —
     корректные пары; в данных нет HTML-символов (теги добавляет рендер);
  2. Рендер: HTML-текст сценария ≤ 4000 символов (лимит edit_text) и содержит
     каждый рекомендуемый код + его ТТХ из MODELS; полная сводка — plain-текст
     и режется answer_long'ом на части ≤ 4000;
  3. Кнопки: «🎯 Сценарии» есть в MAIN_BUTTONS (защита от AI-фолбэка) и в main_kb;
  4. Роутеры: menu < scenarios < fallback в bot.dp (карточки model: из сценариев
     обрабатывает menu; fallback остаётся последним);
  5. Флоу (фейковые Message/CallbackQuery): список → сценарий → назад; scn:full;
     неизвестный scn: → alert.

Запуск: .venv\\Scripts\\python.exe _verify_scenarios.py  (из корня проекта)
"""

import asyncio
import sys

from data import MODELS, SCENARIO_ORDER, SCENARIOS
from handlers.fallback import router as fallback_router
from handlers.menu import router as menu_router
from handlers.scenarios import (
    LIST_TEXT,
    _list_kb,
    _scenario_kb,
    _scenario_text,
    button_scenarios,
    callback_scenario,
    callback_scenario_list,
)
from keyboards import MAIN_BUTTONS, main_kb

FAILS = []


def check(cond, ok_msg, fail_msg):
    if cond:
        print(f"  ✅ {ok_msg}")
    else:
        print(f"  ❌ {fail_msg}")
        FAILS.append(fail_msg)


# ---------- фейки (как в _verify_flow/_test_photo) ----------
class _User:
    id = 42


class _CbMsg:
    def __init__(self):
        self.from_user = _User()
        self.edits = []
        self.answers = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class _Msg:
    def __init__(self):
        self.from_user = _User()
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class _CB:
    def __init__(self, data):
        self.data = data
        self.from_user = _User()
        self.message = _CbMsg()
        self.alerts = []

    async def answer(self, text=None, show_alert=False):
        self.alerts.append((text, show_alert))


def kb_data(kb):
    return [(btn.text, btn.callback_data) for row in kb.inline_keyboard for btn in row]


async def main():
    print("== 1. Данные сценариев ==")
    check(5 <= len(SCENARIOS) <= 7, f"сценариев: {len(SCENARIOS)} (5–7)", f"сценариев {len(SCENARIOS)}, не 5–7")
    check(set(SCENARIO_ORDER) == set(SCENARIOS), "SCENARIO_ORDER соответствует SCENARIOS", "SCENARIO_ORDER != SCENARIOS")
    check(len(SCENARIO_ORDER) == len(set(SCENARIO_ORDER)), "нет дублей в SCENARIO_ORDER", "дубли в SCENARIO_ORDER")

    for sid in SCENARIO_ORDER:
        sc = SCENARIOS[sid]
        bad = []
        for field in ("emoji", "title", "client"):
            if not isinstance(sc.get(field), str) or not sc[field].strip():
                bad.append(field)
        if not (isinstance(sc.get("recommend"), list) and sc["recommend"]):
            bad.append("recommend")
        else:
            for code in sc["recommend"]:
                if code not in MODELS:
                    bad.append(f"recommend:{code}?")
        if not (isinstance(sc.get("why"), list) and sc["why"]):
            bad.append("why")
        qa = sc.get("qa")
        if not (isinstance(qa, list) and qa and all(
                isinstance(p, (tuple, list)) and len(p) == 2 and all(isinstance(s, str) and s.strip() for s in p)
                for p in qa)):
            bad.append("qa")
        extras = sc.get("extras")
        if not (isinstance(extras, list) and all(
                isinstance(p, (tuple, list)) and len(p) == 2 and all(isinstance(s, str) and s.strip() for s in p)
                for p in extras)):
            bad.append("extras")
        # HTML-символы запрещены в данных: теги добавляет только рендер
        strings = [sc[f] for f in ("title", "client", "emoji")] + list(sc["why"])
        strings += [s for pair in sc["qa"] for s in pair]
        strings += [s for pair in sc["extras"] for s in pair]
        if any(ch in s for s in strings for ch in "<>&"):
            bad.append("HTML-символы в данных")
        check(not bad, f"«{sc['title']}» — структура ок", f"{sid}: проблемы {bad}")

    print("== 2. Рендер ==")
    for sid in SCENARIO_ORDER:
        sc = SCENARIOS[sid]
        text = _scenario_text(sid)
        problems = []
        if len(text) > 4000:
            problems.append(f"len={len(text)}>4000")
        for code in sc["recommend"]:
            m = MODELS[code]
            if code not in text:
                problems.append(f"{code} не в тексте")
            for frag in (f"{m['mah']} мА·ч", f"{m['cont']}/{m['peak']} А", m["connector"]):
                if frag not in text:
                    problems.append(f"ТТХ {code}: '{frag}'")
        for section in ("Портрет клиента", "Рекомендуем", "Ключевые преимущества", "Типовые вопросы"):
            if section not in text:
                problems.append(f"раздел «{section}»")
        check(not problems, f"{sid}: рендер ок ({len(text)} симв.)", f"{sid}: {problems}")

    full_text = ("\n\n" + "—" * 30 + "\n\n").join(_scenario_text(s, html=False) for s in SCENARIO_ORDER)
    check("<b>" not in full_text, "полная сводка — plain-текст (без тегов)", "в полной сводке остались теги")
    from utils import split_text
    parts = split_text(full_text)
    check(all(len(p) <= 4000 for p in parts), f"полная сводка режется на {len(parts)} частей ≤ 4000", "часть полной сводки > 4000")

    print("== 3. Кнопки главного меню ==")
    check("🎯 Сценарии" in MAIN_BUTTONS, "«🎯 Сценарии» в MAIN_BUTTONS (не уйдёт в AI-фолбэк)", "«🎯 Сценарии» НЕТ в MAIN_BUTTONS")
    kb_texts = [b.text for row in main_kb.keyboard for b in row]
    check("🎯 Сценарии" in kb_texts, "«🎯 Сценарии» в main_kb", "«🎯 Сценарии» НЕТ в main_kb")

    print("== 4. Порядок роутеров ==")
    import bot
    from handlers.scenarios import router as scenarios_router
    subs = bot.dp.sub_routers

    def idx(r):
        return next(i for i, x in enumerate(subs) if x is r)

    order_ok = idx(menu_router) < idx(scenarios_router) < idx(fallback_router)
    check(order_ok, "menu → scenarios → fallback (model: перехватит menu)", "нарушен порядок menu/scenarios/fallback")
    check(scenarios_router.name == "scenarios", "имя роутера: scenarios", f"имя роутера: {scenarios_router.name!r}")

    print("== 5. Флоу (симуляция) ==")
    msg = _Msg()
    await button_scenarios(msg)
    data = kb_data(_list_kb())
    check(len(msg.answers) == 1 and msg.answers[0][1].get("parse_mode") == "HTML",
          "«🎯 Сценарии» → список (HTML)", "кнопка «🎯 Сценарии» не открывает список")
    scn_cbs = [cb for _, cb in data if cb.startswith("scn:")]
    check(scn_cbs == [f"scn:{sid}" for sid in SCENARIO_ORDER] + ["scn:full"],
          f"кнопки списка: {len(SCENARIO_ORDER)} сценариев + сводка, в порядке SCENARIO_ORDER",
          f"кнопки списка неверны: {scn_cbs}")

    for sid in SCENARIO_ORDER:
        cb = _CB(f"scn:{sid}")
        await callback_scenario(cb)
        problems = []
        if len(cb.message.edits) != 1:
            problems.append("нет edit_text")
        else:
            text, kw = cb.message.edits[0]
            if kw.get("parse_mode") != "HTML":
                problems.append("не HTML")
            if SCENARIOS[sid]["title"] not in text:
                problems.append("нет title")
            if kb_data(kw.get("reply_markup")) != kb_data(_scenario_kb(sid)):
                problems.append("клавиатура != _scenario_kb")
            if any(a[0] is not None for a in cb.alerts):
                problems.append(f"alert с текстом: {cb.alerts}")
            if len(cb.alerts) != 1:
                problems.append(f"callback.answer() вызван {len(cb.alerts)} раз")
        check(not problems, f"scn:{sid} → экран сценария", f"scn:{sid}: {problems}")

    cb = _CB("scn:list")
    await callback_scenario_list(cb)
    ok = (len(cb.message.edits) == 1 and cb.message.edits[0][0] == LIST_TEXT
          and cb.alerts == [(None, False)])
    check(ok, "scn:list → возврат к списку", "scn:list не возвращает список")

    cb = _CB("scn:full")
    await callback_scenario(cb)
    sent = cb.message.answers
    ok = bool(sent) and all(len(t) <= 4000 for t, _ in sent) and sum(len(t) for t, _ in sent) > 8000
    check(ok, f"scn:full → сводка {len(sent) if sent else 0} частями", "scn:full: части не отправлены/слишком длинные")

    cb = _CB("scn:nope")
    await callback_scenario(cb)
    ok = not cb.message.edits and cb.alerts and cb.alerts[0][1] is True
    check(ok, "неизвестный scn: → alert, без правок", "неизвестный scn: обработан неверно")

    print()
    if FAILS:
        print(f"❌ ПРОВАЛЕНО: {len(FAILS)}")
        for f in FAILS:
            print(f"   - {f}")
        sys.exit(1)
    print("✅ ВСЕ ПРОВЕРКИ СЦЕНАРИЕВ ПРОЙДЕНЫ")


if __name__ == "__main__":
    asyncio.run(main())