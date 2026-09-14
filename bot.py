import asyncio
import logging
import os
import re
import json
from datetime import datetime
from logging.handlers import RotatingFileHandler

import aiohttp

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================================
log_formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Логгер для системных сообщений
system_logger = logging.getLogger('system')
system_logger.setLevel(logging.INFO)

# Логгер для диалогов
dialog_logger = logging.getLogger('dialog')
dialog_logger.setLevel(logging.INFO)

# Файл для системных логов (ротация: 5 файлов по 10 МБ)
system_handler = RotatingFileHandler(
    'bot_system.log',
    maxBytes=10*1024*1024,
    backupCount=5,
    encoding='utf-8'
)
system_handler.setFormatter(log_formatter)
system_logger.addHandler(system_handler)

# Файл для диалогов (ротация: 10 файлов по 20 МБ)
dialog_handler = RotatingFileHandler(
    'bot_dialogs.log',
    maxBytes=20*1024*1024,
    backupCount=10,
    encoding='utf-8'
)
dialog_handler.setFormatter(log_formatter)
dialog_logger.addHandler(dialog_handler)

# Дублируем в консоль
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
system_logger.addHandler(console_handler)

logging.basicConfig(level=logging.INFO)

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
AI_PROXY_URL = os.getenv("AI_PROXY_URL", "").strip()
AI_PROXY_TOKEN = os.getenv("AI_PROXY_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PROXY_URL = os.getenv("PROXY_URL", "").strip()

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан. Проверьте файл .env")

# Создаем бота с прокси, если указан
from aiogram.client.session.aiohttp import AiohttpSession

session = None
if PROXY_URL:
    session = AiohttpSession(proxy=PROXY_URL)
    bot = Bot(token=BOT_TOKEN, session=session)
    system_logger.info(f"Bot started with proxy: {PROXY_URL}")
else:
    bot = Bot(token=BOT_TOKEN)
    system_logger.info("Bot started without proxy")

dp = Dispatcher(storage=MemoryStorage())


# ============================================================
# СОСТОЯНИЯ (для обратной связи и калькулятора)
# ============================================================
class FeedbackState(StatesGroup):
    waiting_for_feedback = State()

class BuildState(StatesGroup):
    waiting_for_config = State()


# ============================================================
# ============================================================
# ТТХ МОДЕЛЕЙ
# ============================================================
MODELS = {
    "6S2P": {"mah": 8000, "kg": 0.90, "cont": 70, "peak": 90, "charge": 4, "mohm": 19.5, "w": 45, "best": "Тренировочные и ближние вылеты", "warn": "Не для катушки и груза. Осторожно при резком пилотировании", "drones": ["7","9","10"], "voltage": "6S", "cap_class": "light"},
    "6S3P": {"mah": 12000, "kg": 1.34, "cont": 105, "peak": 135, "charge": 6, "mohm": 13.0, "w": 67, "best": '10" с катушкой 20 км и малым грузом', "warn": "Проверять ток, если дрон тяжёлый или пилотирование резкое", "drones": ["10"], "voltage": "6S", "cap_class": "light"},
    "6S4P": {"mah": 16000, "kg": 1.77, "cont": 140, "peak": 180, "charge": 8, "mohm": 9.8, "w": 89, "best": 'Стандарт 10"; катушка 10 км; рабочие задачи', "warn": None, "drones": ["10"], "voltage": "6S", "cap_class": "standard"},
    "6S5P": {"mah": 20000, "kg": 2.20, "cont": 175, "peak": 225, "charge": 10, "mohm": 7.8, "w": 111, "best": '10" long-range с грузом 1–1,5 кг', "warn": None, "drones": ["10"], "voltage": "6S", "cap_class": "standard"},
    "6S6P": {"mah": 24000, "kg": 2.63, "cont": 210, "peak": 270, "charge": 12, "mohm": 6.5, "w": 133, "best": 'Максимум дальности на 10" без катушки; из практики около 26 км с остатком', "warn": 'Не лучший вариант для 10" с катушкой 20–30 км', "drones": ["10"], "voltage": "6S", "cap_class": "long"},
    "8S2P XT-90-S": {"mah": 8000, "kg": 1.19, "cont": 70, "peak": 90, "charge": 4, "mohm": 26.0, "w": 45, "best": "Минимальный вес, ближний полёт, лёгкие 8S-платформы", "warn": "Не для резкого пилотирования и тяжёлых задач", "drones": ["12","13","15"], "voltage": "8S", "cap_class": "light"},
    "8S2P XT-60": {"mah": 8000, "kg": 1.19, "cont": 70, "peak": 90, "charge": 4, "mohm": 26.0, "w": 45, "best": "Минимальный вес, ближний полёт, лёгкие 8S-платформы", "warn": "Не для резкого пилотирования и тяжёлых задач", "drones": ["10"], "voltage": "8S", "cap_class": "light"},
    "8S3P": {"mah": 12000, "kg": 1.77, "cont": 105, "peak": 135, "charge": 6, "mohm": 17.3, "w": 67, "best": 'Лёгкие 8S-платформы без катушки; спец-10" на 8S', "warn": None, "drones": ["10"], "voltage": "8S", "cap_class": "light"},
    "8S4P": {"mah": 16000, "kg": 2.34, "cont": 140, "peak": 180, "charge": 8, "mohm": 13.0, "w": 89, "best": 'Вход в класс 13"; спец-10" на 8S', "warn": None, "drones": ["10", "13", "hex"], "voltage": "8S", "cap_class": "standard"},  # MODIFIED: добавлен "hex"
    "8S5P": {"mah": 20000, "kg": 2.92, "cont": 175, "peak": 225, "charge": 10, "mohm": 10.4, "w": 111, "best": '13" при ограничении массы; спец-10" на 8S для дальних вылетов', "warn": None, "drones": ["10", "13", "hex"], "voltage": "8S", "cap_class": "standard"},  # MODIFIED: добавлен "hex"
    "8S6P": {"mah": 24000, "kg": 3.50, "cont": 210, "peak": 270, "charge": 12, "mohm": 8.7, "w": 133, "best": 'Оптимум 13"/15": тепловизор, катушка 20 км, возвратная разведка', "warn": 'Не для 10" с катушкой 20–30 км', "drones": ["10", "13", "15", "hex"], "voltage": "8S", "cap_class": "long"},  # MODIFIED: добавлен "hex"
    "8S7P": {"mah": 28000, "kg": 4.07, "cont": 245, "peak": 315, "charge": 14, "mohm": 7.4, "w": 155, "best": '15"/гекса; 13" с большим запасом тяги', "warn": "Тяжёлая; проверять лимит массы носителя", "drones": ["13", "15", "hex"], "voltage": "8S", "cap_class": "long"},
    "8S8P": {"mah": 32000, "kg": 4.65, "cont": 280, "peak": 360, "charge": 16, "mohm": 6.5, "w": 177, "best": '15", гексакоптер, максимальная автономность, катушка 30 км на тяжёлом носителе', "warn": 'Никогда не ставить на стандартную 10"', "drones": ["15", "hex"], "voltage": "8S", "cap_class": "long"},
}

# === КАСКАДНЫЙ ФИЛЬТР ===
# ============================================================
# ДОПУСТИМЫЕ ЗНАЧЕНИЯ ДЛЯ МАШИННОЙ ФИЛЬТРАЦИИ  # NEW
# ============================================================
# PLATFORMS — коды платформ для поля "drones" (дюймы; "hex" — гексакоптер)
PLATFORMS = ["7", "9", "10", "12", "13", "15", "hex"]
# VOLTAGES — ряды напряжения для поля "voltage"
VOLTAGES = ["6S", "8S"]
# CAP_CLASSES — классы ёмкости для поля "cap_class": light ≤12000, standard 12001–20000, long >20000
CAP_CLASSES = ["light", "standard", "long"]

# FILTER_LABELS — человекочитаемые названия для кнопок фильтров
# ("all" — псевдозначение «Все», используется только в UI, не в данных MODELS)
FILTER_LABELS = {
    "drone": {"7": "7\"", "9": "9\"", "10": "10\"", "12": "12\"", "13": "13\"", "15": "15\"", "hex": "Гексакоптер"},
    "voltage": {"6S": "6S (22.2 В)", "8S": "8S (29.6 В)", "all": "Все"},  # MODIFIED: номинал в скобках
    "cap_class": {"light": "Лёгкие (≤12Ач)", "standard": "Стандарт (12–20Ач)", "long": "Макс. дальность (>20Ач)", "all": "Все"},
}

# ============================================================
# КАСКАДНЫЙ ФИЛЬТР: ЧИСТАЯ ЛОГИКА (БЕЗ ПРИВЯЗКИ К ХЕНДЛЕРАМ)  # NEW
# ============================================================
def _apply_filters(drone: str | None = None, voltage: str | None = None, cap_class: str | None = None) -> dict:
    result = dict(MODELS)
    if drone and drone != "all":
        result = {code: m for code, m in result.items() if drone in m["drones"]}
    if voltage and voltage != "all":
        result = {code: m for code, m in result.items() if m["voltage"] == voltage}
    if cap_class and cap_class != "all":
        result = {code: m for code, m in result.items() if m["cap_class"] == cap_class}
    return result


def _build_filter_kb(level: str, drone: str = "all", voltage: str = "all", cap_class: str = "all") -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    if level == "drone":
        for p in PLATFORMS:
            if _apply_filters(drone=p):
                buttons.append([InlineKeyboardButton(
                    text=FILTER_LABELS["drone"][p],
                    callback_data=f"flt:d={p}",
                )])
        # NEW: выход из подбора с первого экрана
        buttons.append([InlineKeyboardButton(text="⬅️ Отмена", callback_data="flt:cancel")])

    elif level == "voltage":
        for v in VOLTAGES + ["all"]:
            if _apply_filters(drone=drone, voltage=v):
                buttons.append([InlineKeyboardButton(
                    text=FILTER_LABELS["voltage"][v],
                    callback_data=f"flt:v={v}:d={drone}",
                )])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="flt:back:drone:d=all")])

    elif level == "cap_class":
        for c in CAP_CLASSES + ["all"]:
            if _apply_filters(drone=drone, voltage=voltage, cap_class=c):
                buttons.append([InlineKeyboardButton(
                    text=FILTER_LABELS["cap_class"][c],
                    callback_data=f"flt:c={c}:v={voltage}:d={drone}",
                )])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"flt:back:voltage:v=all:d={drone}")])

    elif level == "results":
        for code, m in _apply_filters(drone=drone, voltage=voltage, cap_class=cap_class).items():
            buttons.append([InlineKeyboardButton(
                text=f"{code} | {m['mah']}мАч | {m['kg']}кг",
                callback_data=f"fltm:{code}:d={drone}:v={voltage}:c={cap_class}",  # MODIFIED: карточка с возвратом к результатам
            )])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"flt:back:cap_class:c=all:v={voltage}:d={drone}")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# NEW: мульти-фильтр — все секции на одном экране, активные значения помечены «✅ »
def _build_search_kb(drone: str = "all", voltage: str = "all", cap_class: str = "all") -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    def _mark(active: bool, label: str) -> str:
        return f"✅ {label}" if active else label

    # Секция: Напряжение
    buttons.append([InlineKeyboardButton(text="⚡ Напряжение", callback_data="dummy")])
    for v in VOLTAGES + ["all"]:
        buttons.append([InlineKeyboardButton(
            text=_mark(voltage == v, FILTER_LABELS["voltage"][v]),
            callback_data=f"srch:v={v}",
        )])

    # Секция: Ёмкость
    buttons.append([InlineKeyboardButton(text="🔋 Ёмкость", callback_data="dummy")])
    for c in CAP_CLASSES + ["all"]:
        buttons.append([InlineKeyboardButton(
            text=_mark(cap_class == c, FILTER_LABELS["cap_class"][c]),
            callback_data=f"srch:c={c}",
        )])

    # Секция: Платформа (в FILTER_LABELS["drone"] нет "all", поэтому «Все» задаётся литералом)
    buttons.append([InlineKeyboardButton(text="🚁 Платформа", callback_data="dummy")])
    for d in PLATFORMS:
        buttons.append([InlineKeyboardButton(
            text=_mark(drone == d, FILTER_LABELS["drone"][d]),
            callback_data=f"srch:d={d}",
        )])
    buttons.append([InlineKeyboardButton(
        text=_mark(drone == "all", "Все"),
        callback_data="srch:d=all",
    )])

    # Нижние кнопки: показ результатов или индикатор пустого выбора
    if _apply_filters(drone=drone, voltage=voltage, cap_class=cap_class):
        buttons.append([InlineKeyboardButton(text="🔍 Показать результаты", callback_data="srch:show")])
    else:
        buttons.append([InlineKeyboardButton(text="Нет совпадений", callback_data="dummy")])
    buttons.append([InlineKeyboardButton(text="♻️ Сбросить фильтры", callback_data="srch:reset")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

MAIN_BUTTONS = {
    "📊 ТТХ", "🧠 Подбор", "🤖 AI-ассистент", "🛑 Стоп-лист", 
    "📦 Памятка", "🔧 Своя сборка", "📝 Обратная связь"
}

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 ТТХ"), KeyboardButton(text="🤖 AI-ассистент")],
        [KeyboardButton(text="🧠 Подбор"), KeyboardButton(text="🔧 Своя сборка")],
        [KeyboardButton(text="🛑 Стоп-лист"), KeyboardButton(text="📦 Памятка")],
        [KeyboardButton(text="📝 Обратная связь")],
    ],
    resize_keyboard=True,
)


# ============================================================
# HELPERS
# ============================================================
def get_row(model_code: str) -> str:
    return model_code[:2]

def models_by_row(row: str) -> list[str]:
    return [code for code in MODELS if code.startswith(row)]

def split_text(text: str, limit: int = 4000) -> list[str]:
    text = text or ""
    parts = []
    while len(text) > limit:
        cut = text[:limit]
        if "\n" in cut:
            cut = cut[:cut.rfind("\n")]
        parts.append(cut)
        text = text[len(cut):]
    if text:
        parts.append(text)
    return parts or [""]

async def answer_long(message: types.Message, text: str):
    for part in split_text(text):
        await message.answer(part)

async def ask_ai(question: str) -> str:
    if not AI_PROXY_URL:
        system_logger.warning("AI_PROXY_URL not configured")
        return "AI-ассистент ещё не подключён. Заполните AI_PROXY_URL и AI_PROXY_TOKEN в .env"

    headers = {"Content-Type": "application/json"}
    if AI_PROXY_TOKEN:
        headers["Authorization"] = f"Bearer {AI_PROXY_TOKEN}"

    payload = {"question": question, "source": "python-telegram-bot"}
    timeout = aiohttp.ClientTimeout(total=90)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(AI_PROXY_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    system_logger.error(f"AI proxy returned {resp.status}: {body[:500]}")
                    return f"Cloudflare AI вернул ошибку {resp.status}:\n{body[:500]}"

                data = await resp.json()
                answer = data.get("answer")
                if not answer:
                    system_logger.warning("AI returned empty answer")
                    return "AI вернул пустой ответ."
                return answer

    except Exception as e:
        system_logger.exception("AI request failed")
        return f"Ошибка запроса к AI: {e}"

async def answer_ai_with_feedback(message: types.Message, text: str):
    feedback_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👍 Ответ помог", callback_data="fb_good"),
                InlineKeyboardButton(text="👎 Неточно", callback_data="fb_bad"),
            ]
        ]
    )
    
    parts = split_text(text)
    for i, part in enumerate(parts):
        is_last = (i == len(parts) - 1)
        kb = feedback_kb if is_last else None
        try:
            await message.answer(part, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            system_logger.warning(f"Failed to parse HTML: {e}")
            await message.answer(part, reply_markup=kb)

async def handle_ai_request(message: types.Message, question: str):
    """Единая функция для отправки запроса в AI (и для текста, и для голоса)"""
    thinking = await message.answer("⏳ <i>Запрашиваю AI-ассистента...</i>", parse_mode="HTML")
    answer = await ask_ai(question)

    try:
        await thinking.delete()
    except Exception:
        pass

    # ЛОГИРОВАНИЕ: ответ бота
    dialog_logger.info(json.dumps({
        'event': 'bot_response',
        'user_id': message.from_user.id,
        'username': message.from_user.username or 'unknown',
        'original_question': question,
        'answer': answer[:500],  # Обрезаем длинные ответы в логах
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))

    await answer_ai_with_feedback(message, answer)

def format_model(model_code: str) -> str:
    m = MODELS[model_code]
    row = get_row(model_code)

    if row == "6S":
        nominal_v, max_charge_v, work_min_v, abs_min_v = 22.2, 25.2, 18.0, 15.0
        connector, balance, length = "XT60", "JST-XH 7-pin", 133
    else:
        nominal_v, max_charge_v, work_min_v, abs_min_v = 29.6, 33.6, 24.0, 20.0
        connector, balance, length = "XT90-S", "JST-XH 9-pin", 177

    wh = round(nominal_v * m["mah"] / 1000)

    text = f"""
<b>{model_code}</b>

Ёмкость: {m['mah']} мА·ч
Энергия: ~{wh} Вт·ч
Масса: {m['kg']} кг
Напряжение: {row} (Номинал: {nominal_v} В)

Ток длительно: {m['cont']} А
Ток пиково: {m['peak']} А
Ток заряда: {m['charge']} А
Сопротивление: {m['mohm']} мОм

Заряд не выше: {max_charge_v} В
В работе не ниже: {work_min_v} В
Абсолютный минимум: {abs_min_v} В

Разъём: {connector}
Баланс: {balance}
Габариты: {length} × {m['w']} × 74 мм

<b>Применение:</b>
{m['best']}
""".strip()

    if m.get("warn"):
        text += f"\n\n<i>Внимание: {m['warn']}</i>"

    return text


# ============================================================
# HANDLERS: КОМАНДЫ И КНОПКИ
# ============================================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    text = """
<b>FPV Sales Bot</b>

Я помогаю менеджеру быстро находить ТТХ и ответы по стандарту продаж.

<b>Кнопки:</b>
📊 ТТХ — характеристики моделей
🧠 Подбор — быстрый чек-лист подбора
🤖 AI-ассистент — вопрос по клиенту или возражению
🔧 Своя сборка — расчёт нестандартной конфигурации
🛑 Стоп-лист — что нельзя говорить и продавать
📦 Памятка — шаблон для клиента после продажи
📝 Обратная связь — написать администратору

Также я понимаю <b>голосовые сообщения</b>!
""".strip()
    await message.answer(text, reply_markup=main_kb, parse_mode="HTML")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await cmd_start(message)

@dp.message(Command("ai"))
async def cmd_ai(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer("Напишите вопрос после команды /ai.")
        return
    await handle_ai_request(message, args[1].strip())

@dp.message(F.text == "📊 ТТХ")
async def button_ttx(message: types.Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="6S", callback_data="row:6S"),
                InlineKeyboardButton(text="8S", callback_data="row:8S"),
            ]
        ]
    )
    await message.answer("Выберите ряд:", reply_markup=kb)

# === КАСКАДНЫЙ ФИЛЬТР ===
@dp.message(F.text == "🧠 Подбор")  # MODIFIED: был статический чек-лист, теперь точка входа в каскадный фильтр
async def button_selection(message: types.Message):
    # Точка входа в каскадный фильтр: первый уровень — выбор платформы
    await message.answer(
        "🔍 Подбор АКБ\n\nВыберите платформу дрона:",
        reply_markup=_build_filter_kb("drone"),
    )

# NEW: старый статический чек-лист сохранён по команде /checklist
@dp.message(Command("checklist"))
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

@dp.message(F.text == "🛑 Стоп-лист")
async def button_stoplist(message: types.Message):
    text = """
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
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "📦 Памятка")
async def button_memo(message: types.Message):
    text = """
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
    await message.answer(text, parse_mode="HTML")


# ============================================================
# ОБРАТНАЯ СВЯЗЬ (состояние)
# ============================================================
@dp.message(F.text == "📝 Обратная связь")
async def start_feedback(message: types.Message, state: FSMContext):
    await message.answer("Напишите, что нужно улучшить в боте. Я передам это администратору.")
    await state.set_state(FeedbackState.waiting_for_feedback)

@dp.message(FeedbackState.waiting_for_feedback)
async def process_feedback(message: types.Message, state: FSMContext):
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


# ============================================================
# СВОЯ СБОРКА (калькулятор)
# ============================================================
@dp.message(F.text == "🔧 Своя сборка")
async def start_build(message: types.Message, state: FSMContext):
    await message.answer("Введите конфигурацию, например <b>14S12P</b>:", parse_mode="HTML")
    await state.set_state(BuildState.waiting_for_config)

@dp.message(BuildState.waiting_for_config)
async def process_build(message: types.Message, state: FSMContext):
    match = re.match(r"^(\d+)[sS](\d+)[pP]$", message.text.strip())
    if not match:
        await message.answer("Неверный формат. Пример: <b>14S12P</b>", parse_mode="HTML")
        return
        
    s = int(match.group(1))
    p = int(match.group(2))
    
    # Расчеты на основе данных ячейки 21700 (4000 мАч, 3.7В, 72г)
    nom_v = s * 3.7
    max_v = s * 4.2
    work_min_v = s * 3.0
    abs_min_v = s * 2.5
    
    cap_mah = p * 4000
    cap_ah = p * 4
    energy_wh = nom_v * cap_ah
    
    # Масса: ячейки + 40г на силовую часть и упаковку
    weight_kg = s * p * 0.072 + 0.040
    
    cont_a = 35 * p
    peak_a = 45 * p
    charge_a = 2 * p # 0.5C от 4*P Ач = 2*P
    
    total_cells = s * p
    connector = "XT90-S" if s > 6 else "XT60"
    balance_pins = s + 1
    
    text = f"""
<b>Расчёт для {s}S{p}P:</b>

Ячеек всего: {total_cells} шт. (21700)

Ёмкость: {cap_mah} мА·ч ({cap_ah} А·ч)
Энергия: ~{energy_wh:.0f} Вт·ч
Масса сборки: ~{weight_kg:.2f} кг

Напряжение номинал: {nom_v:.1f} В
Заряд (макс): {max_v:.1f} В
Разряд (рабочий): не ниже {work_min_v:.1f} В
Абсолютный минимум: {abs_min_v:.1f} В

Ток длительно: {cont_a} А
Ток пиково: {peak_a} А
Ток заряда (0,5C): {charge_a} А

Разъём: {connector}
Балансир: {balance_pins}-pin
"""
    await message.answer(text, parse_mode="HTML")
    await state.clear()


# ============================================================
# ГОЛОСОВЫЕ СООБЩЕНИЯ
# ============================================================
@dp.message(F.voice)
async def process_voice(message: types.Message):
    # ЛОГИРОВАНИЕ: входящее голосовое
    dialog_logger.info(json.dumps({
        'event': 'user_voice_request',
        'user_id': message.from_user.id,
        'username': message.from_user.username or 'unknown',
        'message_id': message.message_id,
        'duration': message.voice.duration,
        'file_size': message.voice.file_size,
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))
    
    if message.voice.file_size > 20 * 1024 * 1024:
        await message.answer("Голосовое сообщение слишком большое (макс 20 МБ).")
        return
        
    thinking = await message.answer("🎧 Распознаю речь...")
    
    try:
        file = await bot.get_file(message.voice.file_id)
        file_bytes_io = await bot.download_file(file.file_path)
        file_bytes = file_bytes_io.read()
        
        headers = {
            "Authorization": f"Bearer {AI_PROXY_TOKEN}",
            "Content-Type": "application/octet-stream"
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{AI_PROXY_URL}/transcribe", data=file_bytes, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    recognized_text = data.get("text", "").strip()
                    
                    if recognized_text:
                        await thinking.delete()
                        
                        # ЛОГИРОВАНИЕ: распознанный текст
                        dialog_logger.info(json.dumps({
                            'event': 'voice_recognized',
                            'user_id': message.from_user.id,
                            'recognized_text': recognized_text,
                            'timestamp': datetime.now().isoformat()
                        }, ensure_ascii=False))
                        
                        await message.answer(f"🗣 <b>Распознано:</b>\n<i>{recognized_text}</i>", parse_mode="HTML")
                        await handle_ai_request(message, recognized_text)
                    else:
                        await thinking.edit_text("Не удалось распознать речь.")
                else:
                    error_text = await resp.text()
                    system_logger.error(f"Voice recognition error: {resp.status} - {error_text[:100]}")
                    await thinking.edit_text(f"Ошибка распознавания: {resp.status}\n{error_text[:100]}")
                    
    except Exception as e:
        system_logger.exception("Voice processing failed")
        try:
            await thinking.edit_text("Произошла ошибка при обработке голосового сообщения.")
        except:
            pass


# ============================================================
# INLINE: ТТХ
# ============================================================
@dp.callback_query(F.data.startswith("row:"))
async def callback_row(callback: types.CallbackQuery):
    row = callback.data.split(":", 1)[1]
    buttons = [
        [InlineKeyboardButton(text=code, callback_data=f"model:{code}")]
        for code in models_by_row(row)
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(f"Модели ряда {row}:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("model:"))
async def callback_model(callback: types.CallbackQuery):
    model_code = callback.data.split(":", 1)[1]
    row = get_row(model_code)

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⬅️ К списку {row}", callback_data=f"row:{row}")]
        ]
    )
    text = format_model(model_code)
    
    try:
        await callback.message.edit_text(text, reply_markup=back_kb, parse_mode="HTML")
    except Exception:
        await callback.message.edit_text(text, reply_markup=back_kb)
        
    await callback.answer()

# === КАСКАДНЫЙ ФИЛЬТР ===
# ============================================================
# INLINE: КАСКАДНЫЙ ФИЛЬТР ПОДБОРА  # NEW
# ============================================================
@dp.callback_query(F.data.startswith("flt:"))
async def callback_filter(callback: types.CallbackQuery):
    # Форматы callback_data (см. _build_filter_kb):
    #   flt:d={p}                      — выбрана платформа
    #   flt:v={v}:d={d}                — выбрано напряжение
    #   flt:c={c}:v={v}:d={d}          — выбран класс ёмкости
    #   flt:back:{level}:{k}={v}:...   — возврат на уровень level
    parts = callback.data[len("flt:"):].split(":")
    drone, voltage, cap_class = "all", "all", "all"
    level = "drone"

    def _parse_pairs(chunk: list[str]) -> None:
        nonlocal drone, voltage, cap_class
        for part in chunk:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key == "d":
                drone = value
            elif key == "v":
                voltage = value
            elif key == "c":
                cap_class = value

    if parts[0] == "back":
        # Возврат: flt:back:{target_level}:d=...:v=...:c=...
        if len(parts) > 1:
            level = parts[1]
        _parse_pairs(parts[2:])
    elif parts[0] == "cancel":
        # NEW: отмена подбора с первого экрана — убираем инлайн-клавиатуру
        try:
            await callback.message.edit_text("🧠 Подбор отменён.\nНажмите «🧠 Подбор», чтобы начать заново.")
        except Exception as e:
            system_logger.warning(f"Filter cancel failed: {e}")
        await callback.answer()
        return
    elif parts[0].split("=", 1)[0] in {"d", "v", "c"}:
        # Выбор: следующий уровень определяем по ТОМУ, ЧТО выбрал пользователь
        # (d → напряжение, v → ёмкость, c → результаты), а не по итоговому
        # состоянию — иначе кнопка «Все» не двигает каскад вперёд
        key = parts[0].split("=", 1)[0]  # MODIFIED: фикс кнопки «Все»
        _parse_pairs(parts)
        if key == "d":
            level = "voltage"
        elif key == "v":
            level = "cap_class"
        else:  # key == "c"
            level = "results"
    else:
        # Неизвестный формат callback_data — просто закрываем "часики"
        await callback.answer()
        return

    # ЛОГИРОВАНИЕ: шаг каскадного фильтра
    dialog_logger.info(json.dumps({
        'event': 'filter_step',
        'user_id': callback.from_user.id,
        'level': level,
        'drone': drone,
        'voltage': voltage,
        'cap_class': cap_class,
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))

    def _disp(dict_key: str, value: str) -> str:
        # "all" — псевдозначение, которого нет в FILTER_LABELS["drone"]
        if value == "all":
            return "Любая" if dict_key == "drone" else "Все"
        return FILTER_LABELS[dict_key].get(value, value)

    if level == "drone":
        text = "🔍 Подбор АКБ\n\nВыберите платформу дрона:"
    elif level == "voltage":
        text = (
            f"🔍 Подбор АКБ\n\n"
            f"Платформа: {_disp('drone', drone)}\n\n"
            "Выберите напряжение:"
        )
    elif level == "cap_class":
        text = (
            f"🔍 Подбор АКБ\n\n"
            f"Платформа: {_disp('drone', drone)}\n"
            f"Напряжение: {_disp('voltage', voltage)}\n\n"
            "Выберите класс ёмкости:"
        )
    else:  # results
        matches = _apply_filters(drone=drone, voltage=voltage, cap_class=cap_class)
        text = (
            f"🔍 Подбор АКБ — результаты\n\n"
            f"Платформа: {_disp('drone', drone)}\n"
            f"Напряжение: {_disp('voltage', voltage)}\n"
            f"Ёмкость: {_disp('cap_class', cap_class)}\n\n"
            f"Подходящих моделей: {len(matches)}\n\n"
            "Выберите модель:"
        )

    kb = _build_filter_kb(level, drone=drone, voltage=voltage, cap_class=cap_class)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        system_logger.warning(f"Filter edit failed: {e}")

    await callback.answer()

# NEW: карточка модели из результатов каскадного фильтра — с возвратом к результатам.
# (существующие callback_model / callback_row из потока 📊 ТТХ не затронуты)
@dp.callback_query(F.data.startswith("fltm:"))
async def callback_filter_model(callback: types.CallbackQuery):
    # Формат callback_data (см. _build_filter_kb, уровень "results"):
    #   fltm:{code}:d={d}:v={v}:c={c}
    parts = callback.data[len("fltm:"):].split(":")
    model_code = parts[0]
    drone, voltage, cap_class = "all", "all", "all"
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key == "d":
            drone = value
        elif key == "v":
            voltage = value
        elif key == "c":
            cap_class = value

    row = get_row(model_code)
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="⬅️ К результатам",
                callback_data=f"flt:back:results:c={cap_class}:v={voltage}:d={drone}",
            )],
            [InlineKeyboardButton(
                text=f"⬅️ Ко всем моделям {row}",
                callback_data=f"row:{row}",
            )],
        ]
    )
    text = format_model(model_code)

    try:
        await callback.message.edit_text(text, reply_markup=back_kb, parse_mode="HTML")
    except Exception:
        await callback.message.edit_text(text, reply_markup=back_kb)

    await callback.answer()

@dp.callback_query(F.data.in_({"fb_good", "fb_bad"}))
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

@dp.callback_query(F.data == "dummy")
async def dummy_callback(callback: types.CallbackQuery):
    await callback.answer()


# ============================================================
# FALLBACK ДЛЯ ТЕКСТА (отправка в AI)
# ============================================================
@dp.message(lambda m: m.text and not m.text.startswith("/") and m.text not in MAIN_BUTTONS)
async def fallback_ai(message: types.Message):
    # ЛОГИРОВАНИЕ: входящий текстовый запрос
    dialog_logger.info(json.dumps({
        'event': 'user_request',
        'user_id': message.from_user.id,
        'username': message.from_user.username or 'unknown',
        'message_id': message.message_id,
        'text': message.text,
        'timestamp': datetime.now().isoformat()
    }, ensure_ascii=False))
    
    await handle_ai_request(message, message.text)

@dp.message(lambda m: m.text and m.text.startswith("/"))
async def unknown_command(message: types.Message):
    # Игнорируем неизвестные команды
    pass

@dp.message()
async def unsupported(message: types.Message):
    await message.answer("Пока я понимаю только текст и голосовые сообщения.")


# ============================================================
# RUN
# ============================================================
async def main():
    system_logger.info("=" * 50)
    system_logger.info("Bot starting...")
    system_logger.info("=" * 50)
    
    retry_delay = 5
    max_retry_delay = 60
    
    while True:
        try:
            await dp.start_polling(bot)
            break
        except Exception as e:
            system_logger.error(f"Polling error: {e}")
            system_logger.info(f"Restarting in {retry_delay} seconds...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, max_retry_delay)

if __name__ == "__main__":
    asyncio.run(main())