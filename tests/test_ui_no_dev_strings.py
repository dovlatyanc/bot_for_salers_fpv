"""Guard-тест Этапа 9: в UI конструктора нет разработческих строк.

Прогоняет рендеры slot_menu, part_list (с фильтром и без), review (complete
и incomplete), drafts, part_input на реальной tmp-БД (CSV через import_stock_csv)
и проверяет, что в текстах сообщений и текстах служебных кнопок (шапки,
кнопки слотов, пагинация, нижние ряды, статус-блок проверки, подсказка ввода)
НЕТ машинных подстрок: «qty», ключей категорий, «slot:», «callback»,
«кандидат». Имена деталей в проверку НЕ включаются (в них легитимно встречаются
«VTX»/«RX») — для них проверяются только служебные части рендеров.
"""

from db.repositories import list_drafts, list_parts, save_draft
from db.stock_import import import_stock_csv, part_key
from handlers import builder
from states.states import BuilderState
from tests.fakes import FakeCb, FakeMsg, kb_data, make_state

CSV_ROWS = (
    "Рама 10 МАРК4-V2",
    "Мотор axisfly 3115 900KV",
    "Пропеллер HQProp 10Х5Х3 (2CW+2CCW)",
    "Видеопередатчик Axisflying 3W 5.8ГГц",
)

# Ключи-категории, которые НЕ встречаются внутри официальных русских подписей
# (в «(VTX)»/«(VRX)»/«(RX)»/«(TX)» латиница легитимна — эти исключаем).
FORBIDDEN = [
    "qty", "кандидат", "slot:", "callback", "fc_esc_stack",
    "antenna_air", "antenna_ground", "frame", "motor", "prop",
]


def _assert_clean(text: str) -> None:
    lowered = (text or "").lower()
    for token in FORBIDDEN:
        assert token not in lowered, f"разработческая строка «{token}» в тексте:\n{text}"


def _assert_clean_buttons(kb) -> None:
    """Все кнопки, кроме кнопок деталей (b:part:*) — служебные, проверяем тексты."""
    for text, cb in kb_data(kb):
        if cb.startswith("b:part:"):
            continue  # имя детали — легитимный контент кнопки
        _assert_clean(text)


async def _seed(tmp_path) -> None:
    path = tmp_path / "Остатки.csv"
    path.write_text(
        "".join(f"{name}, ;шт;50,000\r\n" for name in CSV_ROWS), encoding="cp1251"
    )
    await import_stock_csv(path)


async def _open_menu(state):
    await builder.cmd_builder(FakeMsg("/builder"), state)
    cb = FakeCb("b:mode:free")
    await builder.cb_mode_free(cb, state)
    return cb


async def test_start_screen_clean():
    state = make_state()
    msg = FakeMsg("/builder")
    await builder.cmd_builder(msg, state)
    text, kwargs = msg.answers[-1]
    _assert_clean(text)
    _assert_clean_buttons(kwargs["reply_markup"])


async def test_slot_menu_clean(tmp_path):
    await _seed(tmp_path)
    state = make_state()
    cb_new = await _open_menu(state)
    # пустое меню: шапка + статус-строки — всё человеческое
    _assert_clean(cb_new.message.edits[-1][0])
    _assert_clean_buttons(cb_new.message.edits[-1][1]["reply_markup"])
    # меню с выбранной рамой: текст содержит имя детали (не проверяем),
    # кнопки слотов — служебные, проверяем их
    sink = _Sink()
    state2 = make_state()
    await state2.update_data(slots={"frame": {"key": part_key(CSV_ROWS[0]), "qty": 1}})
    await builder._show_slot_menu(sink, state2)
    _assert_clean_buttons(_Sink.last_kb)


async def test_part_list_clean(tmp_path):
    await _seed(tmp_path)
    state = make_state()
    await _open_menu(state)
    cb = FakeCb("b:slot:frame")
    await builder.cb_slot(cb, state)
    text, kwargs = cb.message.edits[-1]
    _assert_clean(text)
    _assert_clean_buttons(kwargs["reply_markup"])


async def test_part_input_clean(tmp_path):
    await _seed(tmp_path)
    state = make_state()
    await _open_menu(state)
    await state.update_data(current_slot="frame")
    cb = FakeCb("b:input")
    await builder.cb_input(cb, state)
    _assert_clean(cb.message.edits[-1][0])


async def test_review_clean_complete_and_incomplete(tmp_path):
    await _seed(tmp_path)
    keys = {row: part_key(row) for row in CSV_ROWS}
    parts_by_key = {p["key"]: p for p in await list_parts()}

    # incomplete: только рама
    state = make_state()
    await state.update_data(slots={"frame": {"key": keys[CSV_ROWS[0]], "qty": 1}})
    cb = FakeCb("b:review")
    await builder.cb_review(cb, state)
    text = cb.message.edits[-1][0]
    lines = text.splitlines()
    for line in lines:
        if line.startswith(("📋", "⚠️", "❌", "💡", "✅")):
            _assert_clean(line)
    _assert_clean_buttons(cb.message.edits[-1][1]["reply_markup"])

    # complete: все 4 позиции CSV
    slots = {}
    for row in CSV_ROWS:
        slots[parts_by_key[keys[row]]["category"]] = {"key": keys[row], "qty": 1}
    state = make_state()
    await state.update_data(slots=slots)
    cb2 = FakeCb("b:review")
    await builder.cb_review(cb2, state)
    text2 = cb2.message.edits[-1][0]
    for line in text2.splitlines():
        if line.startswith(("📋", "⚠️", "❌", "💡", "✅")):
            _assert_clean(line)
    _assert_clean_buttons(cb2.message.edits[-1][1]["reply_markup"])


async def test_drafts_screen_clean(tmp_path):
    await _seed(tmp_path)
    state = make_state()
    await _open_menu(state)
    await save_draft(42, {"slots": {"frame": {"key": part_key(CSV_ROWS[0]), "qty": 1}}})
    drafts = await list_drafts(42)
    assert drafts  # черновик создан
    cb = FakeCb("b:start:drafts")
    await builder.cb_start_drafts(cb, state)
    text, kwargs = cb.message.edits[-1]
    _assert_clean(text)
    _assert_clean_buttons(kwargs["reply_markup"])


async def test_hexa_screens_clean(tmp_path):
    """Этап 11в (D3): гекса-экраны (slot_menu с mto-рамой, экран «под заказ»,
    review) — человеческие строки, БЕЗ «рама 15»/дюймовой привязки."""
    await _seed(tmp_path)
    state = make_state()
    await builder.cmd_builder(FakeMsg("/builder"), state)
    await builder.cb_mode_free(FakeCb("b:mode:free"), state)
    cb = FakeCb("b:size:hex")   # свободный Hex = 6 моторов
    await builder.cb_size(cb, state)
    assert await state.get_state() == BuilderState.slot_menu
    menu_text, menu_kwargs = cb.message.edits[-1]
    assert "Рама" in menu_text
    assert "рама 15" not in menu_text.lower()
    _assert_clean(menu_text)
    _assert_clean_buttons(menu_kwargs["reply_markup"])

    # слот frame при 6 моторах → экран «под заказ»
    frame_cb = FakeCb("b:slot:frame")
    await builder.cb_slot(frame_cb, state)
    frame_text, frame_kwargs = frame_cb.message.edits[-1]
    assert "под заказ" in frame_text
    assert "рама 15" not in frame_text.lower()
    _assert_clean(frame_text)
    _assert_clean_buttons(frame_kwargs["reply_markup"])

    # mto-рама → slot_menu + review с mto-строкой
    mto_cb = FakeCb("b:mto_frame")
    await builder.cb_mto_frame(mto_cb, state)
    mto_text, mto_kwargs = mto_cb.message.edits[-1]
    assert "➖ Рама: под заказ (нет на складе)" in mto_text
    assert "рама 15" not in mto_text.lower()
    _assert_clean(mto_text)
    _assert_clean_buttons(mto_kwargs["reply_markup"])
    review_cb = FakeCb("b:review")
    await builder.cb_review(review_cb, state)
    review_text = review_cb.message.edits[-1][0]
    assert "➖ Рама: под заказ (нет на складе)" in review_text
    assert "рама 15" not in review_text.lower()


class _Sink:
    """Ловушка вместо message: пишет последнюю клавиатуру в атрибут класса."""

    last_kb = None

    async def edit_text(self, text, reply_markup=None, **kwargs):
        _Sink.last_kb = reply_markup

    async def answer(self, text, reply_markup=None, **kwargs):
        _Sink.last_kb = reply_markup
