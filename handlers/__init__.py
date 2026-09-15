"""
Пакет обработчиков бота (роутеры aiogram 3, вынесенные из bot.py).

Каждый модуль объявляет свой Router и НЕ импортирует точку входа bot.py —
общие ресурсы (бот, логгеры, переменные окружения) берутся из config,
поэтому зависимости строго в одну сторону и циклических импортов нет:

  handlers/calculator.py — «💰 Калькулятор», «📊 История расчётов», /cancel
                           (FSM MarginState, callback "calc:");
  handlers/menu.py       — /start, /help, /ai, ТТХ (row:/model:), /checklist,
                           стоп-лист, памятка, обратная связь (FSM FeedbackState),
                           оценка ответа AI (fb_good/fb_bad), dummy;
  handlers/scenarios.py  — «🎯 Сценарии» — готовые шпаргалки продаж (callback "scn:");
                           карточки моделей из сценариев открывает handlers/menu.py;
  handlers/followup.py   — «⏰ Напоминания» — личные дела менеджера (FSM FollowUpState,
                           callback "fup:", данные data/followups.json); утренняя
                           сводка в 9:00 — ЭТАП 2 (в модуле есть хуки: DAILY_SEND_HOUR,
                           collect_due);
  handlers/selection.py  — экспресс-подбор «🧠 Подбор» (FSM SelectionState, "sel:");
  handlers/search.py     — мульти-фильтр «🔎 Поиск» (callback "srch:", "srchm:");
  handlers/build.py      — «🔧 Своя сборка» (FSM BuildState, "build_conn:");
  handlers/voice.py      — голосовые сообщения (распознавание через AI-прокси);
  handlers/fallback.py   — fallback: любой текст → AI, неизвестные команды, прочий контент.

Подключение (bot.py, порядок важен — calculator первым, fallback последним):
    dp.include_router(calculator_router)
    ...
    dp.include_router(fallback_router)
"""
