#!/usr/bin/env python3
"""Онлайн-бэкап SQLite-БД бота — запускать перед КАЖДЫМ деплоем (и в cron).

Снимок согласованный даже при работающем боте: sqlite3.Connection.backup()
учитывает WAL (не нужно ни останавливать сервис, ни копировать -wal/-shm).
Только stdlib — работает системным python3 без venv и без импорта проекта
(поэтому парсер sqlite-URL продублирован из db.engine, а .env читается вручную).

Примеры:
    python3 deploy/backup_db.py                  # дефолты: <проект>/data/bot.db → <проект>/data/backups
    python3 deploy/backup_db.py --db /root/fpvbot_data/bot.db --out /root/fpvbot_backups --keep 14

Cron (ежедневно в 08:15):
    15 8 * * * /usr/bin/python3 /root/fpvbot/deploy/backup_db.py --db /root/fpvbot_data/bot.db --out /root/fpvbot_backups --keep 14
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _sqlite_file_from_url(url: str) -> Path | None:
    """Копия логики db.engine._sqlite_file_path (без импорта проекта)."""
    tail = url.split("://", 1)[1] if "://" in url else url
    tail = tail.split("?", 1)[0]
    if not tail or tail.lstrip("/") == ":memory:":
        return None
    if tail.startswith("//"):
        tail = tail[1:]
    elif tail.startswith("/"):
        tail = tail[1:]
    if len(tail) > 2 and tail[2] == ":":
        tail = tail.lstrip("/")
    return Path(tail)


def _read_env_file() -> dict:
    """Минимальный парсер .env (KEY=VALUE): DB_URL на сервере лежит там."""
    env: dict = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip("'\"")
    return env


def resolve_db(arg_db: str | None) -> Path:
    """Приоритет: --db → DB_URL (окружение, затем .env) → <проект>/data/bot.db."""
    if arg_db:
        return Path(arg_db).expanduser().resolve()
    url = (os.getenv("DB_URL", "") or _read_env_file().get("DB_URL", "") or "").strip()
    if url.startswith("sqlite"):
        path = _sqlite_file_from_url(url)
        if path is not None:
            return (path if path.is_absolute() else ROOT / path).resolve()
    return (ROOT / "data" / "bot.db").resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Онлайн-бэкап SQLite-БД бота")
    parser.add_argument("--db", help="путь к bot.db (по умолчанию: DB_URL/.env или data/bot.db)")
    parser.add_argument("--out", help="каталог для копий (по умолчанию: <каталог БД>/backups)")
    parser.add_argument("--keep", type=int, default=14, help="сколько последних копий хранить (по умолчанию 14)")
    args = parser.parse_args()

    src = resolve_db(args.db)
    if not src.exists():
        sys.exit(f"[backup] Файл БД не найден: {src}")

    out_dir = Path(args.out).expanduser().resolve() if args.out else src.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = out_dir / f"bot_{stamp}.db"

    conn = sqlite3.connect(str(src), timeout=30)
    dest = sqlite3.connect(str(dst))
    try:
        conn.backup(dest)  # согласованный снапшот, безопасно на живом боте
    finally:
        dest.close()
        conn.close()

    check = sqlite3.connect(str(dst))
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if integrity != "ok":
        dst.unlink(missing_ok=True)
        sys.exit("[backup] Снапшот не прошёл integrity_check — удалён, копии нет")

    backups = sorted(out_dir.glob("bot_*.db"))
    for old in backups[:-args.keep] if args.keep > 0 else []:
        old.unlink(missing_ok=True)
    kept = len([b for b in backups if b.exists()])
    print(f"[backup] OK: {dst} ({dst.stat().st_size / 1024:.0f} KiB); всего копий: {kept}")


if __name__ == "__main__":
    main()
