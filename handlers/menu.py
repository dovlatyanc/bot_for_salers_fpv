"""
Модуль главного меню (вынесено из bot.py без изменения логики и текстов):
  /start, /help            — приветствие с описанием кнопок;
  /ai <вопрос>             — вопрос AI-ассистенту командой;
  «📊 ТТХ»                 — выбор ряда (row:) и карточки моделей (model:);
  /checklist               — старый статический чек-лист подбора;
  «🛑 Стоп-лист», «📦 Памятка» — статические экраны стандарта продаж (тексты —
                           константы STOPLIST_TEXT / MEMO_TEXT: их же показывает
                           «📚 Шпаргалки» — handlers/cheatsheets.py);
  «📝 Обратная связь»      — FSM FeedbackState, сообщение уходит администратору;
  fb_good / fb_bad         — оценка ответа AI;
  dummy                    — no-op callback (кнопки-«псевдо»).

Общие ресурсы берутся из config (бот, логгеры, ADMIN_ID) — импорта bot.py нет.
"""

import json
from datetime import datetime

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ai import handle_ai_request
from config import ADMIN_ID, bot, dialog_logger, system_logger
from keyboards import MAIN_BUTTONS, main_kb
from states import FeedbackState
from utils import get_row, models_by_row, send_model_card

router = Router(name="menu")


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    text = """
<b>FPV Sales Bot</b>

Я помогаю менеджеру быстро находить ТТХ и ответы по стандарту продаж.

<b>Кнопки:</b>
📊 ТТХ — характеристики моделей
🔎 Поиск — подбор АКБ по фильтрам
💰 Калькулятор — маржа и рекомендация по скидке
🔧 Своя сборка — расчёт нестандартной конфигурации
📚 Шпаргалки — подбор, сценарии, FAQ, возражения, стоп-лист, памятка
🤖 AI-ассистент — вопрос по клиенту или возражению
📝 Обратная связь — написать администратору
⏰ Напоминания — дела по клиентам: сроки и звонки
📊 История расчётов — последние 5 расчётов

Также я понимаю <b>голосовые сообщения</b>!
""".strip()
    await message.answer(text, reply_markup=main_kb, parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await cmd_start(message)

@router.message(Command("ai"))
async def cmd_ai(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer("Напишите вопрос после команды /ai.")
        return
    await handle_ai_request(message, args[1].strip())

@router.message(F.text == "📊 ТТХ")
async def button_ttx(message: types.Message, state: FSMContext):
    # любая кнопка меню — выход из активного FSM-сценария; иначе «зависшее»
    # состояние (например, ввод конфигурации «Своя сборка») перехватывало
    # бы следующие тексты пользователя
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="6S", callback_data="row:6S"),
                InlineKeyboardButton(text="8S", callback_data="row:8S"),
            ]
        ]
    )
    await message.answer("Выберите ряд:", reply_markup=kb)

# NEW: старый статический чек-лист сохранён по команде /checklist
@router.message(Command("checklist"))
async def cmd_checklist(message: types.Message):
    text = """
Быстрый чек-лист подбора:

1. Напряжение и разъём:
- 10" обычно 6S, XT60;
- 13"/15"/гекса — 8S, XT90-S;

2. Задача:
- разведка, катушка, доставка — наш профиль;
- перехват, ПВО, гонки — честный отказ, нужен LiPo.

3. Дальность:
- 1000 мА·ч ≈ 1 км пути;
- если возврат: радиус × 2;
- резерв 20%.

Якоря:
- 6S4P — стандарт 10";
- 6S6P — максимум дальности на 10";
- 8S6P — оптимум для 13"/15".
""".strip()
    await message.answer(text)

# Тексты статических экранов вынесены в константы: их же показывает
# «📚 Шпаргалки» (cheat:stoplist / cheat:memo в handlers/cheatsheets.py),
# чтобы не копипастить.
STOPLIST_TEXT = """
<b>Стоп-факторы и запрещённые формулировки:</b>

<b>Нельзя продавать:</b>
- если клиент хочет перехват, ПВО, гонки;
- если регуляторы не подтверждены под 8S;
- если бюджет массы на 10" меньше 1,3 кг.

<b>Нельзя говорить:</b>
- «Пролетит ровно 24 км»;
- «Выдержит 500 циклов»;
- «Наши ячейки лучше конкурентов»;
- «BMS не нужна»;
- «Сами виноваты».
""".strip()

MEMO_TEXT = """
<b>Шаблон памятки клиенту:</b>

Здравствуйте! Спасибо за покупку АКБ Li-Ion 21700.
Платы защиты в батарее нет — как у всех полётных батарей.

1. ЗАРЯД: балансное ЗУ с режимом Li-Ion (4,20 В на ячейку), алгоритм CC/CV.
2. ТОК ЗАРЯДА: 0,5C (2P — 4 А, 3P — 6 А, 4P — 8 А, 5P — 10 А, 6P — 12 А).
3. ТЕМПЕРАТУРА ЗАРЯДА: только при 0…+45 °C. Заряд на морозе снимает гарантию.
4. РАЗРЯД: 6S не ниже 18,0 В (абс. минимум 15,0 В). 8S не ниже 24,0 В (абс. минимум 20,0 В).
5. ПОЛЁТ: Летать можно при –20…+60 °C. Зимой батарею везти в тепле.

После первого заряда пришлите фото экрана зарядника с напряжениями групп.
""".strip()

@router.message(F.text == "🛑 Стоп-лист")
async def button_stoplist(message: types.Message, state: FSMContext):
    # выход из активного FSM-сценария (см. button_ttx)
    await state.clear()
    await message.answer(STOPLIST_TEXT, parse_mode="HTML")

@router.message(F.text == "📦 Памятка")
async def button_memo(message: types.Message, state: FSMContext):
    # выход из активного FSM-сценария (см. button_ttx)
    await state.clear()
    await message.answer(MEMO_TEXT, parse_mode="HTML")

# ============================================================
# AI-АССИСТЕНТ (кнопка меню)
# ============================================================
@router.message(F.text == "🤖 AI-ассистент")
async def button_ai_assistant(message: types.Message, state: FSMContext):
    # Раньше эта кнопка не обрабатывалась вовсе: fallback пропускает тексты из
    # MAIN_BUTTONS, а FSM-сценарии (например, ввод конфигурации «Своя сборка»)
    # перехватывали её текст («Неверный формат»). Теперь нажатие выходит из
    # любого сценария и просит написать вопрос: свободный текст и так уходит
    # в AI через fallback, отдельное FSM-состояние для вопроса не нужно.
    await state.clear()
    dialog_logger.info(json.dumps({
        'event': 'ai_assistant_open',
        'user_id': message.from_user.id,
        'username': message.from_user.username or 'unknown',
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))
    await message.answer(
        "🤖 <b>AI-ассистент</b>\n\n"
        "Напишите ваш вопрос одним сообщением — отвечу по клиентам, возражениям "
        "и продукту.\n<i>Например: клиент просит 8S8P на 10″ — что ответить?</i>",
        parse_mode="HTML",
    )


# ============================================================
# ОБРАТНАЯ СВЯЗЬ (состояние)
# ============================================================
FEEDBACK_BUTTON = "📝 Обратная связь"
FEEDBACK_EXIT_WORDS = frozenset({"отмена", "cancel", "❌", "❌ отмена"})


@router.message(F.text == FEEDBACK_BUTTON)
async def start_feedback(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Напишите, что нужно улучшить в боте. Я передам это администратору.")
    await state.set_state(FeedbackState.waiting_for_feedback)

@router.message(FeedbackState.waiting_for_feedback, F.text)
async def process_feedback(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw == FEEDBACK_BUTTON:
        # повторная кнопка — перезапуск сценария (хендлер зарегистрирован выше)
        await start_feedback(message, state)
        return
    if raw in MAIN_BUTTONS or raw.lower() in FEEDBACK_EXIT_WORDS:
        # кнопка меню или «отмена» во время ввода — чистый выход;
        # раньше текст кнопки уходил администратору как «отзыв»
        await state.clear()
        await message.answer("❌ Обратная связь закрыта. Выберите раздел в меню.")
        return
    # ЛОГИРОВАНИЕ: обратная связь от пользователя
    dialog_logger.info(json.dumps({
        'event': 'user_feedback_to_admin',
        'user_id': message.from_user.id,
        'username': message.from_user.username or 'unknown',
        'feedback_text': message.text,
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))

    if ADMIN_ID:
        try:
            user_info = f"{message.from_user.id} (@{message.from_user.username or 'без_ник'})"
            await bot.send_message(ADMIN_ID, f"📝 <b>Фидбек от {user_info}:</b>\n\n{message.text}", parse_mode="HTML")
            await message.answer("Спасибо! Ваш отзыв передан администратору.")
        except Exception as e:
            system_logger.error(f"Feedback send error: {e}")
            await message.answer("Произошла ошибка при отправке отзыва.")
    else:
        await message.answer("ADMIN_ID не настроен в .env")
    await state.clear()


@router.message(FeedbackState.waiting_for_feedback)
async def process_feedback_not_text(message: types.Message):
    # не-текст (фото и т.п.) во время ввода — мягкая подсказка (как в faq/followup)
    await message.answer("Опишите предложение текстом одним сообщением.")


# ============================================================
# КАРТОЧКИ МОДЕЛЕЙ (из экрана «📊 ТТХ»)
# ============================================================
@router.callback_query(F.data.startswith("row:"))
async def callback_row(callback: types.CallbackQuery):
    row = callback.data.split(":", 1)[1]
    buttons = [
        [InlineKeyboardButton(text=code, callback_data=f"model:{code}")]
        for code in models_by_row(row)
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(f"Модели ряда {row}:", reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("model:"))
async def callback_model(callback: types.CallbackQuery):
    model_code = callback.data.split(":", 1)[1]
    row = get_row(model_code)

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⬅️ К списку {row}", callback_data=f"row:{row}")]
        ]
    )
    # MODIFIED: карточка через send_model_card (фото + подпись, если фото есть)
    await send_model_card(callback, model_code, back_kb, bot)

    await callback.answer()


# ============================================================
# ОЦЕНКА ОТВЕТА AI (fb_good / fb_bad) и no-op кнопки
# ============================================================
@router.callback_query(F.data.in_({"fb_good", "fb_bad"}))
async def process_ai_feedback(callback: types.CallbackQuery):
    # ЛОГИРОВАНИЕ: обратная связь на ответ AI
    dialog_logger.info(json.dumps({
        'event': 'user_feedback',
        'user_id': callback.from_user.id,
        'username': callback.from_user.username or 'unknown',
        'feedback': callback.data,
        'message_id': callback.message.message_id,
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))

    if callback.data == "fb_good":
        new_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Спасибо за оценку!", callback_data="dummy")]]
        )
    else:
        new_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📝 Принято, улучшим промпт", callback_data="dummy")]]
        )

    try:
        await callback.message.edit_reply_markup(reply_markup=new_kb)
    except Exception as e:
        system_logger.warning(f"Failed to edit reply markup: {e}")

    await callback.answer()

@router.callback_query(F.data == "dummy")
async def dummy_callback(callback: types.CallbackQuery):
    await callback.answer()


