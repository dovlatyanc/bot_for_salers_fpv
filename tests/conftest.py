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

import pytest  # noqa: E402

import db  # noqa: E402
from db.seed import DEFAULT_SOURCES, SeedSources  # noqa: E402


@pytest.fixture(autouse=True)
async def _db(tmp_path):
    """Изолированная SQLite-БД на каждый тест (файл во временном каталоге).

    Справочники prices/faq и история расчётов переносятся из проектных JSON —
    тесты калькулятора и FAQ написаны против этих данных. follow_ups остаётся
    пустой: тесты follow-up ждут точные списки адресатов, а чужие записи из
    data/followups.json (реальный админ) их бы сломали.
    """
    db.init_engine(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    await db.init_db(sources=SeedSources(
        calc_history=DEFAULT_SOURCES.calc_history,
        prices=DEFAULT_SOURCES.prices,
        faq=DEFAULT_SOURCES.faq,
    ))
    yield
    await db.close_db()