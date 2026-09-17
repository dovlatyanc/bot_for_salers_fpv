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
  handlers/cheatsheets.py — «📚 Шпаргалки» — inline-меню справочных разделов
                           (callback "cheat:"): подбор, сценарии, FAQ, возражения,
                           стоп-лист, памятка; экраны переиспользуют show-функции
                           и тексты остальных роутеров;
  handlers/scenarios.py  — «🎯 Сценарии» — готовые шпаргалки продаж (callback "scn:");
                           карточки моделей из сценариев открывает handlers/menu.py;
  handlers/followup.py   — «⏰ Напоминания» — личные дела менеджера (FSM FollowUpState,
                           callback "fup:", таблицы followups/settings пакета db); система напоминаний о follow-up:
                           reminders_loop (запускается из bot.py) шлёт утреннюю сводку в DAILY_SEND_HOUR:00 —
                           🔴 просроченные + 🟡 на сегодня; ключ last_daily в таблице settings защищает от дублей;
                           upcoming_loop шлёт предварительные напоминания за 10/5/2 минуты до событий
                           с точным временем (due_at); журнал followup_reminders защищает от дублей;
  handlers/faq.py        — «❓ FAQ» — база частых вопросов клиентов (callback "faq:",
                           FSM FaqState для поиска; таблицы faq_categories/faq_questions пакета db);
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
