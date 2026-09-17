#!/usr/bin/env python3
"""Сборка релиз-архива БЕЗ прод-данных и секретов.

Архив физически не содержит data/bot.db* (живая БД менеджеров), .env, логи,
venv, __pycache__, node_modules, .git, dist. Поэтому unzip -o на сервере
не может перезаписать БД — пункт «отделить прод-данные от кода» закрыт
самим форматом поставки. Кроссплатформенно (stdlib, Windows/Linux).

Запуск из корня проекта (или как есть — пути считаются от файла):
    python deploy/make_release.py            # → dist/fpvbot_YYYYmmdd_HHMMSS.zip
"""

import fnmatch
import hashlib
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# Каталоги, которые целиком не попадают в архив (проверяется по частям пути)
EXCLUDE_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache",
                "node_modules", "dist", "backups"}
# Файлы-секреты и runtime-данные: по имени или маске от корня архива
EXCLUDE_NAMES = {".env", ".dev.vars"}
EXCLUDE_GLOBS = ("*.log", "*.py[cod]", "data/bot.db*", "data/backups/*",
                 "config/calc_history.json")


def excluded(rel_parts: tuple, name: str, posix: str) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel_parts):
        return True
    if name in EXCLUDE_NAMES:
        return True
    return any(fnmatch.fnmatch(posix, glob) for glob in EXCLUDE_GLOBS)


def main() -> None:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"fpvbot_{stamp}.zip"
    DIST.mkdir(exist_ok=True)
    target = DIST / name

    files = [
        (path, path.relative_to(ROOT))
        for path in sorted(ROOT.rglob("*"))
        if path.is_file()
        and not excluded(path.relative_to(ROOT).parts[:-1],
                         path.name, path.relative_to(ROOT).as_posix())
    ]

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, rel in files:
            zf.write(path, rel.as_posix())

    # Защита от будущих правок списков исключений: архива с прод-данными
    # и секретами быть не должно — валидируем содержимое после сборки.
    forbidden = []
    with zipfile.ZipFile(target) as zf:
        for entry in zf.namelist():
            if (entry == ".env" or entry.endswith((".log", ".pyc"))
                    or fnmatch.fnmatch(entry, "data/bot.db*")
                    or fnmatch.fnmatch(entry, "data/backups/*")
                    or "__pycache__" in entry.split("/")):
                forbidden.append(entry)
    if forbidden:
        target.unlink()
        sys.exit(f"[release] В архив попали запрещённые файлы — удалён: {forbidden}")

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"[release] OK: {target}")
    print(f"[release] Файлов: {len(files)}, размер: {target.stat().st_size / 1024:.0f} KiB")
    print(f"[release] SHA-256: {digest}")
    print("[release] Проверки: без .env, без *.log, без data/bot.db* — unzip -o на сервере безопасен.")


if __name__ == "__main__":
    main()
