"""
Модуль «📚 Шпаргалки» — единая точка входа в справочные разделы.

В главном меню справочные кнопки собраны под одной reply-кнопкой «📚 Шпаргалки»;
разделы открываются inline-меню с callback_data префикса cheat:*:

  «📚 Шпаргалки» (reply) — inline-меню из семи разделов;
  cheat:menu             — то же inline-меню правкой сообщения: кнопка «⬅️ В
                           шпаргалки» на экранах разделов (навигация всегда
                           «экран → меню разделов → главное меню»);
  cheat:ttx              — каталог моделей (выбор ряда row: → карточки model:,
                           экран бывшей кнопки «📊 ТТХ»);
  cheat:selection        — первый экран «Подбора» (SEL_STEP_A_TEXT +
                           _sel_step_a_kb из handlers/selection.py). Состояние
                           SelectionState здесь НЕ выставляется («без set_state»)
                           — FSM «подхватывается» при нажатии sel:a:* (ветка "a"
                           callback_selection принимает и несброшенное состояние);
  cheat:scenarios        — список сценариев (показ через _show_list из
                           handlers/scenarios.py, как у scn:list);
  cheat:faq              — главный экран FAQ (рендер/правка из handlers/faq.py,
                           как у faq:menu);
  cheat:objections       — список возражений (показ через _show_list из
                           handlers/objections.py, как у obj:list);
  cheat:stoplist         — текст стоп-листа (константа STOPLIST_TEXT из
                           handlers/menu.py — та же, что у reply-кнопки);
  cheat:memo             — текст памятки (константа MEMO_TEXT из handlers/menu.py);
  cheat:back             — в главное меню с экрана меню разделов: текст /start +
                           reply-клавиатура main_kb (cmd_start из handlers/menu.py).

Префикс cheat: уникален и не пересекается с row:/model:/scn:/srch:/srchm:/sel:/
fup:/fb_*/dummy. Существующие reply-хендлеры разделов (button_selection,
button_scenarios, button_faq, button_objections, button_stoplist, button_memo)
сохранены: они продолжают работать для «старых» кнопок в закэшированных
клавиатурах клиентов.

Логирование: dialog_logger, JSON cheatsheet_open {user_id, name},
name ∈ menu|selection|scenarios|faq|objections|stoplist|memo.
"""

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import system_logger
from handlers.faq import (
    _edit as _faq_edit,
    _main_kb as _faq_main_kb,
    _render_main as _faq_render_main,
    get_categories as get_faq_categories,
)
from handlers.menu import (
    MEMO_TEXT, STOPLIST_TEXT, TTX_SELECT_ROW_TEXT, cmd_start, ttx_rows_kb,
)
from handlers.objections import _show_list as show_objections_list
from handlers.scenarios import _show_list as show_scenarios_list
from handlers.selection import SEL_STEP_A_TEXT, _sel_step_a_kb
from utils.ui import edit_or_send, log_event

router = Router(name="cheatsheets")

CHEATSHEET_BUTTON = "📚 Шпаргалки"
CHEATSHEET_TEXT = "📚 <b>Шпаргалки менеджера</b>\n\nЧто нужно?"

#: Разделы inline-меню: (суффикс callback_data cheat:<…>, подпись кнопки).
#: Порядок кортежа задаёт раскладку меню (2 колонки; 7-й раздел —
#: в последнем ряду один).
SECTIONS: tuple[tuple[str, str], ...] = (
    ("ttx", "📊 Каталог моделей"),
    ("selection", "🧠 Подбор АКБ"),
    ("scenarios", "🎯 Сценарии"),
    ("faq", "❓ FAQ"),
    ("objections", "💬 Возражения"),
    ("stoplist", "🛑 Стоп-лист"),
    ("memo", "📦 Памятка"),
)


# ============================================================
# КЛАВИАТУРЫ
# ============================================================
def _cheat_kb() -> InlineKeyboardMarkup:
    """Inline-меню «Шпаргалок»: разделы в 2 колонки + «⬅️ В меню»."""
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"cheat:{name}")
         for name, label in SECTIONS[i:i + 2]]
        for i in range(0, len(SECTIONS), 2)
    ]
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="cheat:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back_kb() -> InlineKeyboardMarkup:
    """Кнопка «назад» на статических экранах (стоп-лист, памятка) — в меню
    разделов «📚 Шпаргалки», как с остальных экранов (см. cheat:menu)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В шпаргалки", callback_data="cheat:menu")],
    ])


# ============================================================
# ХЕНДЛЕРЫ
# ============================================================
@router.message(F.text == CHEATSHEET_BUTTON)
async def button_cheatsheets(message: types.Message, state: FSMContext):
    """«📚 Шпаргалки» — inline-меню справочных разделов."""
    # выход из активного FSM-сценария (см. button_ttx в handlers/menu.py)
    await state.clear()
    await message.answer(CHEATSHEET_TEXT, reply_markup=_cheat_kb(), parse_mode="HTML")


@router.callback_query(F.data == "cheat:menu")
async def callback_cheat_menu(callback: types.CallbackQuery):
    """⬅️ В шпаргалки — возврат в inline-меню разделов с экранов разделов
    (сценарии, FAQ, возражения, каталог, подбор, стоп-лист, памятка).

    edit_or_send вместо «edit + warning»: кнопка стоит и на карточках моделей
    (сообщение с фото), где edit_text невозможен — там меню откроется новым
    сообщением, а не «умрёт»."""
    log_event(callback.from_user.id, "cheatsheet_open", {"name": "menu"})
    await edit_or_send(
        callback.message, CHEATSHEET_TEXT,
        reply_markup=_cheat_kb(), warn_prefix="Cheatsheet menu edit failed",
    )
    await callback.answer()


@router.callback_query(F.data == "cheat:selection")
async def callback_cheat_selection(callback: types.CallbackQuery, state: FSMContext):
    """🧠 Подбор АКБ — первый экран. set_state здесь НЕ вызывается: FSM
    SelectionState подхватывается при нажатии sel:a:* (см. selection.py)."""
    log_event(callback.from_user.id, "cheatsheet_open", {"name": "selection"})
    await state.clear()  # как при обычном «🧠 Подбор» — старт с чистого состояния
    try:
        await callback.message.edit_text(SEL_STEP_A_TEXT, reply_markup=_sel_step_a_kb())
    except Exception as e:
        system_logger.warning(f"Cheatsheet selection edit failed: {e}")
    await callback.answer()


@router.callback_query(F.data == "cheat:ttx")
async def callback_cheat_ttx(callback: types.CallbackQuery):
    """📊 Каталог моделей — экран бывшей кнопки «📊 ТТХ»: выбор ряда,
    дальше цепочка row: → model: работает как раньше (handlers/menu.py)."""
    log_event(callback.from_user.id, "cheatsheet_open", {"name": "ttx"})
    try:
        await callback.message.edit_text(
            TTX_SELECT_ROW_TEXT, reply_markup=ttx_rows_kb(),
        )
    except Exception as e:
        system_logger.warning(f"Cheatsheet ttx edit failed: {e}")
    await callback.answer()


@router.callback_query(F.data == "cheat:scenarios")
async def callback_cheat_scenarios(callback: types.CallbackQuery):
    """🎯 Сценарии — тот же список, что у кнопки «🎯 Сценарии» / scn:list."""
    log_event(callback.from_user.id, "cheatsheet_open", {"name": "scenarios"})
    await show_scenarios_list(callback.message, edit=True)
    await callback.answer()


@router.callback_query(F.data == "cheat:faq")
async def callback_cheat_faq(callback: types.CallbackQuery, state: FSMContext):
    """❓ FAQ — главный экран (категории + поиск), как у faq:menu."""
    log_event(callback.from_user.id, "cheatsheet_open", {"name": "faq"})
    await state.clear()  # как button_faq: активный поиск FAQ закрывается
    categories = await get_faq_categories()
    await _faq_edit(callback, _faq_render_main(categories), _faq_main_kb(categories))
    await callback.answer()


@router.callback_query(F.data == "cheat:objections")
async def callback_cheat_objections(callback: types.CallbackQuery):
    """💬 Возражения — тот же список, что у кнопки «💬 Возражения» / obj:list."""
    log_event(callback.from_user.id, "cheatsheet_open", {"name": "objections"})
    await show_objections_list(callback.message, edit=True)
    await callback.answer()


@router.callback_query(F.data == "cheat:stoplist")
async def callback_cheat_stoplist(callback: types.CallbackQuery):
    """🛑 Стоп-лист — тот же текст, что у reply-кнопки «🛑 Стоп-лист»."""
    log_event(callback.from_user.id, "cheatsheet_open", {"name": "stoplist"})
    try:
        await callback.message.edit_text(
            STOPLIST_TEXT, reply_markup=_back_kb(), parse_mode="HTML",
        )
    except Exception as e:
        system_logger.warning(f"Cheatsheet stoplist edit failed: {e}")
    await callback.answer()


@router.callback_query(F.data == "cheat:memo")
async def callback_cheat_memo(callback: types.CallbackQuery):
    """📦 Памятка — тот же текст, что у reply-кнопки «📦 Памятка»."""
    log_event(callback.from_user.id, "cheatsheet_open", {"name": "memo"})
    try:
        await callback.message.edit_text(
            MEMO_TEXT, reply_markup=_back_kb(), parse_mode="HTML",
        )
    except Exception as e:
        system_logger.warning(f"Cheatsheet memo edit failed: {e}")
    await callback.answer()


@router.callback_query(F.data == "cheat:back")
async def callback_cheat_back(callback: types.CallbackQuery):
    """⬅️ В меню — главное меню: текст /start + reply-клавиатура main_kb
    (reply-клавиатуру нельзя показать через edit_text — шлём сообщением)."""
    await cmd_start(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("cheat:"))
async def callback_cheat_unknown(callback: types.CallbackQuery):
    """Неизвестный cheat:* — защита от протухших кнопок (регистрируется последним)."""
    await callback.answer("Раздел не найден", show_alert=True)