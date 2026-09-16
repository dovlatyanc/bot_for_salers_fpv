"""Общая настройка тестов: окружение задается ДО первого импорта config.py.

.env проекта не изменяется и не требуется: переменные задаются через
os.environ.setdefault до импорта config (load_dotenv не перекрывает уже
установленные переменные окружения). Конкретные значения .env для тестов
не важны: AI-провайдеры конструируются явно, HTTP подменяется фейками.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Минимум переменных, чтобы config.py поднялся без .env (BOT_TOKEN обязателен)
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("AI_PROVIDER", "cloudflare")
os.environ.setdefault("AI_PROXY_URL", "")
os.environ.setdefault("AI_PROXY_TOKEN", "")