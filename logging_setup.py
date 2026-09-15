"""
Настройка логирования бота (вынесено из bot.py).

- 'system' — системные события: файл bot_system.log (ротация: 5 файлов по 10 МБ) + консоль;
- 'dialog' — диалоги: файл bot_dialogs.log (ротация: 10 файлов по 20 МБ).

Идемпотентно: повторный вызов не добавляет дублирующие хендлеры.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(
    system_log: Path = Path("bot_system.log"),
    dialog_log: Path = Path("bot_dialogs.log"),
) -> None:
    # Повторный вызов (например, при реимпорте в тестах) — без дублей хендлеров
    if logging.getLogger("system").handlers:
        return

    log_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Логгер для системных сообщений
    system_logger = logging.getLogger('system')
    system_logger.setLevel(logging.INFO)

    # Логгер для диалогов
    dialog_logger = logging.getLogger('dialog')
    dialog_logger.setLevel(logging.INFO)

    # Файл для системных логов
    system_handler = RotatingFileHandler(
        system_log,
        maxBytes=10*1024*1024,
        backupCount=5,
        encoding='utf-8'
    )
    system_handler.setFormatter(log_formatter)
    system_logger.addHandler(system_handler)

    # Файл для диалогов
    dialog_handler = RotatingFileHandler(
        dialog_log,
        maxBytes=20*1024*1024,
        backupCount=10,
        encoding='utf-8'
    )
    dialog_handler.setFormatter(log_formatter)
    dialog_logger.addHandler(dialog_handler)

    # Дублируем системные в консоль
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    system_logger.addHandler(console_handler)

    logging.basicConfig(level=logging.INFO)
