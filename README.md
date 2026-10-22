# bot_for_salers_fpv

Telegram-бот для продавцов FPV-дронов и комплектующих: калькулятор маржи, каталог и подбор АКБ, «Своя сборка», конструктор дронов по остаткам 1С, сценарии продаж, возражения, FAQ, follow-up-напоминания и AI-ассистент (текст + голосовые).

## Возможности

- 💰 **Калькулятор маржи** — FSM-расчёт по прайсу, скидки/объёмы, история расчётов
- 📦 **Каталог АКБ** — ряды 6S/8S, карточки моделей с фото, экспресс-подбор и мульти-фильтр поиск
- 🔧 **Своя сборка** — свободный ввод («12В 12000мА·ч», «8S», «90А») → бот переводит параметры в S/P и доспрашивает недостающее
- 🛠 **Конструктор дронов** — FSM-сборка борта по 13 слотам из боевой БД остатков: классификация CSV 1С, подбор кандидатов, проверка совместимости L1–L4, черновики, 12 производственных рецептов
- 🎯 **Сценарии продаж, 💬 возражения, ❓ FAQ** — категории, поиск, стоп-лист, /checklist
- ⏰ **Напоминания (follow-up)** — CRUD личных дел, утренняя сводка в 9:00, напоминания «за 10/5/2 минуты»
- 🤖 **AI-ассистент** — fallback на любой текст, распознавание голосовых, оценка ответов 👍/👎 с обратной связью админу
- 🛡 **Админ-контур** — /stats, /add_faq, /update_price, /load_stock, /classify_pending, /add_alias, /compat_rules, /stock_stats, /reclassify, /recipes

## Стек

Python 3.10+ · aiogram 3.13+ (FSM — кастомный SQLAlchemyStorage в БД, переживает рестарт) · SQLAlchemy 2 async + aiosqlite (SQLite `data/bot.db`; переход на PostgreSQL — через `DB_URL` в `.env`) · python-dotenv · aiohttp · pytest.

AI-бэкенды (выбор через `AI_PROVIDER` в `.env`): Cloudflare AI Worker ([fpv-ai-proxy](fpv-ai-proxy/) — отдельный Node/Cloudflare Worker для проксирования AI и раздачи фото моделей) или YandexGPT + SpeechKit.

## Быстрый старт

```bash
git clone https://github.com/dovlatyanc/bot_for_salers_fpv.git
cd bot_for_salers_fpv

python -m venv .venv
.venv\Scripts\activate            # Windows; Linux/macOS: source .venv/bin/activate

pip install -r requirements.txt

copy .env.example .env            # Linux/macOS: cp .env.example .env
# заполните BOT_TOKEN и ADMIN_ID (описание всех переменных — в .env.example)

python bot.py
```

База SQLite (`data/bot.db`) создаётся и наполняется автоматически при первом запуске.

## Тесты

```bash
python -m pytest -q
```

Точечные проверки: `python -X utf8 _verify_<имя>.py` (см. `_verify_*.py` в корне).

## Документация и развёртывание

- `PROJECT_STRUCTURE.txt` — подробное описание логики и дерева проекта
- `deploy/README.md` — установка на VPS (systemd), `deploy/QUICKSTART.md`, `deploy/watchdog.py` — серверный watchdog
- `fpv-ai-proxy/` — Cloudflare Worker для AI-прокси

## Примечания

- `.env`, логи (`*.log*`), живая БД (`data/bot.db*`) и бэкапы в git не попадают — см. `.gitignore`.
