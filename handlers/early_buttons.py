"""Роутер «ранних» кнопок главного меню — подключается ПЕРВЫМ, раньше FSM-сценариев.

Кнопки ниже обязаны открывать свой раздел из любой точки диалога. Раньше их
перехватывал активный FSM-сценарий (роутер калькулятора стоит первым): нажатие
во время расчёта отвечало «❌ Калькулятор закрыт…», а раздел не открывался —
кнопку приходилось жать второй раз (выглядело как «странное поведение»).
Сейчас нажатие всегда завершает активный сценарий (state.clear() внутри
реализации) и сразу открывает раздел:

  «🤖 AI-ассистент»  — реализация button_ai_assistant (handlers/menu.py);
  «📚 Шпаргалки»     — реализация button_cheatsheets (handlers/cheatsheets.py).

Реализация одна — здесь она только регистрируется раньше остальных роутеров
(см. bot.py). Повторная регистрация на «родных» роутерах остаётся как failsafe:
кто бы ни сработал первым, поведение одно и то же.
"""

from aiogram import F, Router

from handlers.cheatsheets import CHEATSHEET_BUTTON, button_cheatsheets
from handlers.menu import button_ai_assistant

router = Router(name="early_buttons")

# раньше FSM-сценариев (bot.py включает этот роутер первым)
router.message.register(button_ai_assistant, F.text == "🤖 AI-ассистент")
router.message.register(button_cheatsheets, F.text == CHEATSHEET_BUTTON)