"""logging_setup: ротация файлов логов (регрессия аудита п. 1.2).

Файлы bot_system.log / bot_dialogs.log не должны расти бесконечно:
system — RotatingFileHandler 5 файлов × 10 МБ, dialog — 10 × 20 МБ.
config.py вызывает setup_logging() при импорте — хендлеры уже установлены.
"""

import logging
from logging.handlers import RotatingFileHandler

from logging_setup import setup_logging


def _file_handler(name: str) -> RotatingFileHandler:
    handlers = [
        h for h in logging.getLogger(name).handlers
        if isinstance(h, RotatingFileHandler)
    ]
    assert len(handlers) == 1, f"ожидался ровно один файловый хендлер у '{name}'"
    return handlers[0]


def test_system_log_rotation():
    h = _file_handler("system")
    assert h.maxBytes == 10 * 1024 * 1024
    assert h.backupCount == 5
    assert h.encoding == "utf-8"


def test_dialog_log_rotation():
    h = _file_handler("dialog")
    assert h.maxBytes == 20 * 1024 * 1024
    assert h.backupCount == 10
    assert h.encoding == "utf-8"


def test_setup_logging_is_idempotent():
    before = {n: len(logging.getLogger(n).handlers) for n in ("system", "dialog")}
    setup_logging()      # повторный вызов не должен добавлять хендлеров
    after = {n: len(logging.getLogger(n).handlers) for n in ("system", "dialog")}
    assert before == after
