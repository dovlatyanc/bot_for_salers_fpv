"""Админ-контур «🛠 Конструктора» (Этап 8): импорт остатков, разметка, правила.

Хендлеры вызываются напрямую с фейками (паттерн test_admin_handlers.py);
CSV-документ — FakeDocument/FakeMsg.download (fakes.py). ADMIN_ID=1 задан
в conftest; не-админ получает тишину. БД изолирована на каждый тест
(autouse-фикстура _db в conftest.py), сид 5 правил совместимости входит в init_db.
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import handlers.admin as admin  # noqa: F401 — проверка импортируемости роутера
from sqlalchemy import update

from db.engine import get_sessionmaker
from db.models import Part
from db.repositories import (
    list_aliases,
    list_compat_rules,
    list_parts,
    list_snapshots,
)
from db.stock_import import import_stock_csv
from handlers.admin import (
    AdminState,
    cb_compat_add,
    cb_compat_toggle,
    cmd_add_alias,
    cmd_aliases,
    cmd_classify_pending,
    cmd_compat_rules,
    cmd_del_alias,
    cmd_load_stock,
    cmd_reclassify,
    cmd_recipes,
    cmd_stock_stats,
    cmd_stats,
    process_rule_desc,
    process_rule_params,
    process_rule_type,
    stock_file_received,
    stock_not_document,
)
from keyboards.builder import kb_compat_rules
from tests.fakes import FakeDocument, FakeMsg, FakeUser, kb_data, make_state

ADMIN = SimpleNamespace(id=1, username="boss", first_name="Босс")
NOT_ADMIN = FakeUser()  # id=42

# Фикстуры как в tests/test_builder_repos.py (cp1251, «Номенклатура;Ед;Кол-во»):
# v1 → v2: +рама 13 (added), -термоусадка (removed), VTX 11,000 → 8,000 (changed).
CSV_V1 = (
    "DMKR 7.2G 5W VTX, ;шт;11,000\r\n"
    "Антенна 5.8, ;шт;1,000\r\n"
    "Рама 10 дюймов, ;шт;2,000\r\n"
    "Мотор axisfly 3115 900KV, ;шт;4,000\r\n"
    "Пропеллер HQProp 10X5X3, ;шт;3,000\r\n"
    "Термоусадочная пленка ПВХ, ;кг;5,000\r\n"
)
CSV_V2 = (
    "DMKR 7.2G 5W VTX, ;шт;8,000\r\n"
    "Антенна 5.8, ;шт;1,000\r\n"
    "Рама 10 дюймов, ;шт;2,000\r\n"
    "Рама 13 дюймов, ;шт;1,000\r\n"
    "Мотор axisfly 3115 900KV, ;шт;4,000\r\n"
    "Пропеллер HQProp 10X5X3, ;шт;3,000\r\n"
)


def _admin_msg(text=None, document=None) -> FakeMsg:
    return FakeMsg(text=text, document=document, user=ADMIN)


def _non_admin_msg(text=None, document=None) -> FakeMsg:
    return FakeMsg(text=text, document=document, user=NOT_ADMIN)


def _doc(name: str, csv_text: str) -> FakeDocument:
    return FakeDocument(name, csv_text.encode("cp1251"))


def _last(msg: FakeMsg) -> str:
    return msg.answers[-1][0]


async def _seed_csv(tmp_path, name: str, csv_text: str) -> None:
    """Импорт фикстуры напрямую через import_stock_csv (минуя хендлер)."""
    path = tmp_path / name
    path.write_text(csv_text, encoding="cp1251")
    await import_stock_csv(path)


async def _set_category(key: str, category: str) -> None:
    """Прямая пометка категории позиции (обход классификатора) — для «other»."""
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Part).where(Part.key == key).values(category=category, params={})
        )
        await session.commit()


def _fake_cb(data: str, user=ADMIN) -> SimpleNamespace:
    """CallbackQuery-фейк с message.edits/answers и answer() → alerts."""
    cb = SimpleNamespace(
        data=data, from_user=user,
        message=SimpleNamespace(edits=[], answers=[]),
        alerts=[],
    )

    async def _answer(text=None, show_alert=False):
        cb.alerts.append((text, show_alert))

    async def _edit_text(text, **kwargs):
        cb.message.edits.append((text, kwargs))

    async def _msg_answer(text, **kwargs):
        cb.message.answers.append((text, kwargs))

    cb.answer = _answer
    cb.message.edit_text = _edit_text
    cb.message.answer = _msg_answer
    return cb


# ---------- Группа 1: доступ — только ADMIN_ID ----------

async def test_builder_commands_silent_for_non_admin():
    state = make_state()
    msg = _non_admin_msg(text="/x")
    for cmd in (cmd_load_stock, cmd_classify_pending, cmd_add_alias,
                cmd_aliases, cmd_del_alias, cmd_compat_rules,
                cmd_stock_stats, cmd_reclassify, cmd_recipes):
        if cmd is cmd_load_stock:
            await cmd(msg, state)
        else:
            await cmd(msg)
        assert msg.answers == [], f"{cmd.__name__} ответил не-админу"
    # callback'и: не-админ — тишина (даже без callback.answer)
    cb_toggle = _fake_cb("cr:toggle:1", user=NOT_ADMIN)
    await cb_compat_toggle(cb_toggle)
    assert cb_toggle.alerts == [] and cb_toggle.message.edits == []
    cb_add = _fake_cb("cr:add", user=NOT_ADMIN)
    state = make_state()
    await cb_compat_add(cb_add, state)
    assert cb_add.alerts == [] and cb_add.message.answers == []
    assert await state.get_state() is None


async def test_kb_compat_rules_layout():
    kb = kb_compat_rules([
        {"id": 3, "rule_type": "a_rule", "is_active": True},
        {"id": 7, "rule_type": "b_rule", "is_active": False},
    ])
    assert kb_data(kb) == [
        ("✅ Правило #3", "cr:toggle:3"),
        ("⛔ Правило #7", "cr:toggle:7"),
        ("➕ Добавить правило", "cr:add"),
    ]


# ---------- Группа 2: /load_stock ----------

async def test_load_stock_first_import_report_and_tmpdir_clean(tmp_path):
    state = make_state()
    msg = _admin_msg(text="/load_stock")
    await cmd_load_stock(msg, state)
    assert "CSV" in _last(msg) and "/cancel" in _last(msg)
    assert await state.get_state() == AdminState.waiting_stock_file

    doc_msg = _admin_msg(document=_doc("stock.csv", CSV_V1))
    before = {p.name for p in Path(tempfile.gettempdir()).iterdir()
              if p.name.startswith("fpv_stock_")}
    await stock_file_received(doc_msg, state)
    report = _last(doc_msg)
    assert "✅ Остатки загружены" in report
    assert "Позиций: 6, новых: 6, обнулено: 0, пропущено: 0" in report
    assert "(первая)" in report                       # один снапшот — без diff
    assert await state.get_state() is None            # FSM сброшена после приёма
    after = {p.name for p in Path(tempfile.gettempdir()).iterdir()
             if p.name.startswith("fpv_stock_")}
    assert after == before                            # временный файл удалён (finally)
    snaps = await list_snapshots(limit=2)
    assert len(snaps) == 1 and f"#{snaps[0]['id']}" in report


async def test_load_stock_second_import_shows_diff_and_other_hint(tmp_path):
    await _seed_csv(tmp_path, "v1.csv", CSV_V1)
    # Позицию, которой НЕТ в v2, помечаем «other»: импорт не пересчитывает
    # отсутствующие в файле — подсказка /classify_pending появится в отчёте.
    await _set_category("термоусадочная пленка пвх", "other")
    state = make_state()
    await cmd_load_stock(_admin_msg(text="/load_stock"), state)
    msg = _admin_msg(document=_doc("stock_v2.csv", CSV_V2))
    await stock_file_received(msg, state)
    report = _last(msg)
    # В v2 шесть строк: термоусадка удалена (7-я позиция каталога остаётся с 0).
    assert "Позиций: 6, новых: 1, обнулено: 1, пропущено: 0" in report
    assert "(пред. #" in report
    assert "Изменения остатков: 1 позиция" in report
    assert "DMKR 7.2G 5W VTX: 11 → 8 (-3)" in report
    assert "Без категории: 1 → /classify_pending" in report


async def test_load_stock_rejects_non_csv_and_text_in_step(tmp_path):
    state = make_state()
    await cmd_load_stock(_admin_msg(text="/load_stock"), state)
    msg = _admin_msg(document=_doc("price.txt", "не csv"))
    await stock_file_received(msg, state)
    assert "не CSV" in _last(msg)
    assert await state.get_state() is None
    assert await list_snapshots(limit=2) == []        # импорта не было
    # текст в шаге ожидания файла: подсказка, «отмена» — выход
    state2 = make_state()
    await cmd_load_stock(_admin_msg(text="/load_stock"), state2)
    text_msg = _admin_msg(text="держи файл")
    await stock_not_document(text_msg, state2)
    assert "Жду CSV" in _last(text_msg)
    assert await state2.get_state() == AdminState.waiting_stock_file
    cancel_msg = _admin_msg(text="отмена")
    await stock_not_document(cancel_msg, state2)
    assert "Отменено" in _last(cancel_msg)
    assert await state2.get_state() is None


# ---------- Группа 3: /classify_pending ----------

async def test_classify_pending_empty_and_lists_other(tmp_path):
    msg = _admin_msg(text="/classify_pending")
    await cmd_classify_pending(msg)
    assert "Нераспознанных позиций нет" in _last(msg)

    await _seed_csv(tmp_path, "v1.csv", CSV_V1)
    # одну позицию помечаем «other» (классификатор сам классифицирует всё CSV):
    await _set_category("рама 10 дюймов", "other")
    msg = _admin_msg(text="/classify_pending")
    await cmd_classify_pending(msg)
    report = _last(msg)
    assert "Нераспознанные позиции (1)" in report
    assert "• Рама 10 дюймов — остаток 2" in report   # человекочитаемый список
    assert "/add_alias <категория по-русски> <название>" in report
    assert "Рама, Моторы" in report                   # подписи категорий (Этап 9)


# ---------- Группа 4: /add_alias ----------

async def test_add_alias_reclassifies_part_and_upserts(tmp_path):
    await _seed_csv(tmp_path, "v1.csv", CSV_V1)
    # Ре-разметка: автоклассификатор отнёс антенну к antenna_air (дефолт),
    # админ переносит её в antenna_ground — отчёт показывает changed=1.
    msg = _admin_msg(text="/add_alias antenna_ground Антенна 5.8")
    await cmd_add_alias(msg)
    report = _last(msg)
    assert "Алиас «антенна 5.8» → Антенна наземная (#1)" in report
    assert "изменено позиций: 1" in report

    part = next(p for p in await list_parts() if p["key"] == "антенна 5.8")
    assert part["category"] == "antenna_ground"       # override применился сразу
    assert await list_parts(category="antenna_air") == []
    aliases = await list_aliases()
    assert len(aliases) == 1
    assert aliases[0]["category_override"] == "antenna_ground"
    assert aliases[0]["part_key"] == "антенна 5.8"

    # повторный вызов с той же категорией — upsert той же строки, changed=0
    msg2 = _admin_msg(text="/add_alias ANTENNA_GROUND Антенна 5.8")
    await cmd_add_alias(msg2)
    assert "изменено позиций: 0" in _last(msg2)
    assert len(await list_aliases()) == 1
    part = next(p for p in await list_parts() if p["key"] == "антенна 5.8")
    assert part["category"] == "antenna_ground"


async def test_add_alias_validates_format_and_category(tmp_path):
    msg = _admin_msg(text="/add_alias")
    await cmd_add_alias(msg)
    assert "Формат: /add_alias <категория по-русски> <название>" in _last(msg)

    msg = _admin_msg(text="/add_alias rocket Мотор axisfly")
    await cmd_add_alias(msg)
    assert "Неизвестная категория «rocket»" in _last(msg)
    assert await list_aliases() == []                 # ничего не создано


async def test_add_alias_without_part_yet_keeps_hint(tmp_path):
    msg = _admin_msg(text="/add_alias spare Совсем новая деталь XYZ")
    await cmd_add_alias(msg)
    assert "алиас применится при следующем /load_stock" in _last(msg)
    aliases = await list_aliases()
    assert len(aliases) == 1 and aliases[0]["part_key"] is None
    assert aliases[0]["category_override"] == "spare"


# ---------- Группа 5: /aliases и /del_alias ----------

async def test_aliases_list_and_delete(tmp_path):
    msg = _admin_msg(text="/aliases")
    await cmd_aliases(msg)
    assert "Алиасов нет" in _last(msg)

    await _seed_csv(tmp_path, "v1.csv", CSV_V1)
    msg = _admin_msg(text="/add_alias consumable Термоусадочная пленка ПВХ")
    await cmd_add_alias(msg)

    msg = _admin_msg(text="/aliases")
    await cmd_aliases(msg)
    listing = _last(msg)
    assert "Алиасы (1)" in listing
    assert "#1 термоусадочная пленка пвх → Расходники" in listing  # подпись категории

    msg = _admin_msg(text="/del_alias abc")
    await cmd_del_alias(msg)
    assert "Формат: /del_alias <id>" in _last(msg)

    msg = _admin_msg(text="/del_alias 1")
    await cmd_del_alias(msg)
    assert "удалён" in _last(msg)
    assert await list_aliases() == []

    msg = _admin_msg(text="/del_alias 1")
    await cmd_del_alias(msg)
    assert "не найден" in _last(msg)


# ---------- Группа 6: /compat_rules (список, toggle, добавление) ----------

async def test_compat_rules_screen_lists_seeded_rules():
    msg = _admin_msg(text="/compat_rules")
    await cmd_compat_rules(msg)
    text, kwargs = msg.answers[0]
    assert "Правила совместимости" in text
    assert "(5)" in text                              # сид init_db: 5 правил
    assert "#1" in text and "#5" in text
    kb = kwargs["reply_markup"]
    buttons = kb_data(kb)
    assert len(buttons) == 6                          # 5 toggle + «➕»
    assert all(data.startswith("cr:toggle:") or data == "cr:add"
               for _, data in buttons)
    assert ("➕ Добавить правило", "cr:add") in buttons


async def test_compat_toggle_flips_rule_and_rerenders_screen():
    cb = _fake_cb("cr:toggle:1")
    await cb_compat_toggle(cb)
    rules = await list_compat_rules(active_only=False)
    assert rules[0]["is_active"] is False             # было включено → выключено
    text, kwargs = cb.message.edits[-1]
    assert "Правила совместимости" in text
    assert "⛔ #1" in text                            # перерисовка с новым статусом
    assert "reply_markup" in kwargs
    assert cb.alerts == [("⛔ Отключено", False)]
    # повторный toggle — обратно включается
    cb2 = _fake_cb("cr:toggle:1")
    await cb_compat_toggle(cb2)
    rules = await list_compat_rules(active_only=False)
    assert rules[0]["is_active"] is True
    assert cb2.alerts == [("✅ Включено", False)]


async def test_compat_toggle_unknown_id_alerts_and_keeps_state():
    cb = _fake_cb("cr:toggle:999")
    await cb_compat_toggle(cb)
    assert cb.alerts and "999" in cb.alerts[0][0]
    assert cb.alerts[0][1] is True                    # show_alert
    assert cb.message.edits == []                     # экран не перерисовался
    cb_bad = _fake_cb("cr:toggle:abc")
    await cb_compat_toggle(cb_bad)
    assert cb_bad.alerts and "id" in cb_bad.alerts[0][0]


async def test_compat_add_fsm_full_flow():
    state = make_state()
    cb = _fake_cb("cr:add")
    await cb_compat_add(cb, state)
    assert await state.get_state() == AdminState.waiting_rule_type
    assert "шаг 1/3" in cb.message.answers[-1][0]

    m1 = _admin_msg(text="vtx_vrx_same_band")
    await process_rule_type(m1, state)
    assert await state.get_state() == AdminState.waiting_rule_params
    assert "шаг 2/3" in _last(m1)

    m2 = _admin_msg(text='{"slots": ["vtx", "vrx"]}')
    await process_rule_params(m2, state)
    assert await state.get_state() == AdminState.waiting_rule_desc
    assert "шаг 3/3" in _last(m2)

    m3 = _admin_msg(text="Видеотракт в одном диапазоне")
    await process_rule_desc(m3, state)
    assert await state.get_state() is None
    assert "добавлено и включено" in _last(m3)

    rules = await list_compat_rules(active_only=False)
    rule = next(r for r in rules if r["rule_type"] == "vtx_vrx_same_band")
    assert rule["params"] == {"slots": ["vtx", "vrx"]}
    assert rule["description"] == "Видеотракт в одном диапазоне"
    assert rule["is_active"] is True


async def test_compat_add_fsm_rejects_invalid_json_and_non_object():
    state = make_state()
    cb = _fake_cb("cr:add")
    await cb_compat_add(cb, state)
    await process_rule_type(_admin_msg(text="band_rule"), state)

    m_bad = _admin_msg(text="{slots")                 # не JSON → шаг повторяется
    await process_rule_params(m_bad, state)
    assert "Это не JSON" in _last(m_bad)
    assert await state.get_state() == AdminState.waiting_rule_params

    m_list = _admin_msg(text='["vtx", "vrx"]')        # JSON, но не объект
    await process_rule_params(m_list, state)
    assert "объектом" in _last(m_list)
    assert await state.get_state() == AdminState.waiting_rule_params
    assert len(await list_compat_rules(active_only=False)) == 5   # ничего не создано

    m_ok = _admin_msg(text='{"slots": ["vtx", "vrx"]}')
    await process_rule_params(m_ok, state)
    assert await state.get_state() == AdminState.waiting_rule_desc


async def test_compat_add_fsm_cancel_midway_creates_nothing():
    state = make_state()
    cb = _fake_cb("cr:add")
    await cb_compat_add(cb, state)
    await process_rule_type(_admin_msg(text="band_rule"), state)
    await process_rule_params(_admin_msg(text='{"slots": ["vtx", "vrx"]}'), state)
    cancel = _admin_msg(text="отмена")
    await process_rule_desc(cancel, state)
    assert "Отменено" in _last(cancel)
    assert await state.get_state() is None
    assert len(await list_compat_rules(active_only=False)) == 5


async def test_compat_add_rule_without_description():
    state = make_state()
    await cb_compat_add(_fake_cb("cr:add"), state)
    await process_rule_type(_admin_msg(text="quiet_rule"), state)
    await process_rule_params(_admin_msg(text='{"min_ah": 4}'), state)
    msg = _admin_msg(text="-")                        # «-» — без описания
    await process_rule_desc(msg, state)
    rules = await list_compat_rules(active_only=False)
    rule = next(r for r in rules if r["rule_type"] == "quiet_rule")
    assert rule["description"] is None
    assert rule["params"] == {"min_ah": 4}


# ---------- Группа 7: /stock_stats ----------

async def test_stock_stats_needs_two_snapshots():
    msg = _admin_msg(text="/stock_stats")
    await cmd_stock_stats(msg)
    assert "минимум две выгрузки" in _last(msg)


async def test_stock_stats_reports_consumption_between_snapshots(tmp_path):
    await _seed_csv(tmp_path, "v1.csv", CSV_V1)
    await _seed_csv(tmp_path, "v2.csv", CSV_V2)
    snaps = await list_snapshots(limit=2)
    msg = _admin_msg(text="/stock_stats")
    await cmd_stock_stats(msg)
    text = _last(msg)
    assert f"#{snaps[1]['id']} → #{snaps[0]['id']}" in text
    assert "Убывания: 1 позиция, всего -3" in text
    assert "Пополнения: 0 позиций, всего +0" in text
    assert "Новые позиции: 1, исчезли: 1" in text
    assert "Топ изменений" in text
    assert "DMKR 7.2G 5W VTX: 11 → 8 (-3)" in text


# ---------- Группа 8: /reclassify ----------

async def test_reclassify_reports_and_is_idempotent(tmp_path):
    await _seed_csv(tmp_path, "v1.csv", CSV_V1)
    msg = _admin_msg(text="/reclassify")
    await cmd_reclassify(msg)
    assert "Пересчёт позиций: 6, изменено:" in _last(msg)

    msg2 = _admin_msg(text="/reclassify")
    await cmd_reclassify(msg2)
    assert "изменено: 0" in _last(msg2)               # идемпотентность

    # позиция с нераспознаваемым именем переживает reclassify и попадает
    # в список «other» (прямая вставка Part в БД):
    async with get_sessionmaker()() as session:
        session.add(Part(key="непонятная деталь", name="Непонятная деталь",
                         unit="шт", category="other", params={}))
        await session.commit()
    msg3 = _admin_msg(text="/reclassify")
    await cmd_reclassify(msg3)
    text3 = _last(msg3)
    assert "Пересчёт позиций: 7," in text3
    assert "Без категории: 1" in text3
    assert "• Непонятная деталь" in text3


# ---------- Группа 9: блок «Конструктор» в /stats ----------

async def test_stats_includes_builder_block(tmp_path):
    await _seed_csv(tmp_path, "v1.csv", CSV_V1)
    msg = _admin_msg(text="/stats")
    await cmd_stats(msg)
    text = _last(msg)
    assert "🛠 <b>Конструктор</b>" in text
    assert "Частей всего: <b>7</b>" in text
    assert "категорий: <b>" in text                   # точный состав категорий — за классификатором
    assert "Выгрузка остатков: #1 от " in text
    assert "Черновиков: <b>0</b>" in text


# ---------- Группа 10: /recipes ----------

async def test_cmd_recipes_lists_builds_and_unresolved():
    """📦 /recipes: 13 рецептов (Этап 10в: + гекса 15″), «можно собрать:
    N шт», ⚠️ при part_key=None."""
    msg = _admin_msg(text="/recipes")
    await cmd_recipes(msg)
    report = _last(msg)
    assert "Серийные конфигурации (13)" in report      # весь RECIPE_DICT
    assert "— можно собрать: 0 шт" in report           # каталог пуст → 0
    assert "⚠️ Слотов вне каталога:" in report          # сид при пустом parts
    assert "нет в каталоге:" in report                 # имена слотов показаны
    assert "/add_alias" in report                      # подсказка админу