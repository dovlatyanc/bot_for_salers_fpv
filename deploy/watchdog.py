#!/usr/bin/env python3
"""
Watchdog для FPV Sales Bot: периодическая проверка живости + перезапуск при зависании.

Запускается на сервере независимо от SSH-сессии — через systemd-таймер
fpvbot-watchdog.timer (или cron, см. deploy/README.md). Ноль зависимостей
(только python3 stdlib), поэтому работает под системным python3, без venv.

Что проверяется (run_checks):
  1) systemd-юнит (WATCHDOG_UNIT, по умолчанию fpvbot) в статусе «active»;
  2) heartbeat (HEARTBEAT_FILE из .env): bot.py пишет туда метку времени каждые
     HEARTBEAT_INTERVAL=60 с; файл просрочен (старше HEARTBEAT_MAX_AGE,
     по умолчанию 900 с) или отсутствует → цикл событий бота завис
     (systemd такое не видит: процесс жив, но не отвечает);
  3) проба getMe — информационная: доступность сети Telegram/токена. Если красна
     ТОЛЬКО она — рестарт не делается (не починит канал), только лог.

При обнаружении проблемы: рестарт (WATCHDOG_RESTART_CMD, по умолчанию
«systemctl restart <unit>»), повторная проверка, уведомление ADMIN_ID через
Telegram API напрямую (не через бота — его как раз перезапустили).

Код возврата: 0 — здоров/успешно перезапущен; 1 — нездоров, рестарт не
помог (или --dry-run); 2 — зарезервировано.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # корень проекта (deploy/..)
TELEGRAM_API = "https://api.telegram.org"

# Настройки: приоритет — реальное окружение процесса, затем файл .env
SETTING_KEYS = (
    "BOT_TOKEN", "ADMIN_ID", "PROXY_URL",
    "HEARTBEAT_FILE", "HEARTBEAT_MAX_AGE",
    "WATCHDOG_UNIT", "WATCHDOG_RESTART_CMD", "WATCHDOG_NOTIFY",
)


def log(msg: str) -> None:
    """Строка в stdout с меткой времени (journald утащит в журнал)."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_env(path: Path) -> dict:
    """Минимальный парсер .env: KEY=VALUE, комментарии «#», парные кавычки срезаются."""
    values = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def effective_settings(env_path: Path) -> dict:
    """Слитые настройки: окружение процесса имеет приоритет над файлом .env."""
    merged = load_env(env_path)
    merged.update({k: os.environ[k] for k in SETTING_KEYS if os.environ.get(k)})
    return merged


def check_service(unit: str):
    """Статус юнита через «systemctl is-active»; None — systemctl нет (не Linux)."""
    if not unit or shutil.which("systemctl") is None:
        return None
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None


def heartbeat_age_seconds(path_str: str):
    """Возраст heartbeat-файла в секундах; None — файла нет/путь не задан."""
    path_str = (path_str or "").strip()
    if not path_str:
        return None
    hb = Path(path_str)
    if not hb.is_file():
        return None
    return max(0.0, time.time() - hb.stat().st_mtime)


def _int_setting(cfg: dict, key: str, default: int) -> int:
    try:
        return int((cfg.get(key) or "").strip() or default)
    except ValueError:
        log(f"{key}={cfg.get(key)!r} — не число, беру {default}")
        return default


def run_checks(cfg: dict):
    """Возвращает (healthy, problems, telegram_info)."""
    problems = []
    unit = (cfg.get("WATCHDOG_UNIT") or "").strip()
    state = check_service(unit)
    if state is not None and state != "active":
        problems.append(f"systemd: юнит {unit} в статусе «{state}»")

    hb_path = (cfg.get("HEARTBEAT_FILE") or "").strip()
    if hb_path:
        max_age = _int_setting(cfg, "HEARTBEAT_MAX_AGE", 900)
        age = heartbeat_age_seconds(hb_path)
        if age is None:
            problems.append("heartbeat-файл отсутствует (бот не записал метку)")
        elif age > max_age:
            problems.append(
                f"heartbeat просрочен: {int(age)} с > {max_age} с "
                "(цикл событий бота завис?)"
            )
    else:
        log("HEARTBEAT_FILE не задан — проверка зависания отключена (добавь путь в .env)")

    info = telegram_get_me(cfg.get("BOT_TOKEN") or "")
    return (not problems), problems, info


def _urlopen(url: str, data=None, timeout: int = 15):
    """urlopen с учётом PROXY_URL (поддерживается только http/https-прокси)."""
    proxy = (os.environ.get("PROXY_URL") or "").strip()
    if proxy.startswith(("http://", "https://")):
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        return opener.open(url, data=data, timeout=timeout)
    return urllib.request.urlopen(url, data=data, timeout=timeout)


def telegram_get_me(token: str):
    """Проба getMe — информационная: доступность сети Telegram и валидность токена."""
    if not token:
        return True, "getMe пропущен (нет BOT_TOKEN)"
    try:
        with _urlopen(f"{TELEGRAM_API}/bot{token}/getMe", timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — любая сетевая ошибка = «канал красный»
        return False, f"getMe: {type(e).__name__}: {e}"
    if payload.get("ok"):
        username = payload.get("result", {}).get("username", "?")
        return True, f"getMe: @{username}"
    return False, f"getMe: {payload.get('description', 'неизвестная ошибка')}"


def telegram_notify(token: str, chat_id: str, text: str) -> bool:
    """Уведомление админу напрямую через Bot API (сам бот как раз перезапускается)."""
    if not token or not chat_id:
        log("Уведомление в Telegram пропущено (нет BOT_TOKEN/ADMIN_ID в .env)")
        return False
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    try:
        with _urlopen(f"{TELEGRAM_API}/bot{token}/sendMessage", data=data) as resp:
            ok = bool(json.loads(resp.read().decode("utf-8")).get("ok"))
    except Exception as e:  # noqa: BLE001
        log(f"Уведомление в Telegram не отправлено: {type(e).__name__}: {e}")
        return False
    if not ok:
        log("Уведомление отклонено API Telegram (проверьте ADMIN_ID)")
    return ok


def restart_service(cfg: dict) -> bool:
    """Рестарт: WATCHDOG_RESTART_CMD или «systemctl restart <WATCHDOG_UNIT>»."""
    unit = (cfg.get("WATCHDOG_UNIT") or "").strip()
    cmd = (cfg.get("WATCHDOG_RESTART_CMD") or "").strip()
    if not cmd and unit and shutil.which("systemctl"):
        cmd = f"systemctl restart {unit}"
    if not cmd:
        log("Нет способа рестарта: задайте WATCHDOG_RESTART_CMD "
            "(сложную команду — через bash -c '…')")
        return False
    log(f"Перезапускаю: {cmd}")
    try:
        proc = subprocess.run(
            shlex.split(cmd), capture_output=True, text=True, timeout=180
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"Команда рестарта упала: {type(e).__name__}: {e}")
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[:300]
        log(f"Рестарт завершился с кодом {proc.returncode}: {tail}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Watchdog для FPV Sales Bot")
    parser.add_argument("--env", default=str(BASE_DIR / ".env"),
                        help="путь к .env бота (по умолчанию ../.env от deploy/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="только проверка: без рестарта и уведомлений")
    args = parser.parse_args()

    env_path = Path(args.env).expanduser()
    cfg = effective_settings(env_path)
    if not env_path.is_file():
        log(f"Предупреждение: .env не найден: {env_path}")

    healthy, problems, info = run_checks(cfg)
    log(f"Telegram: {info[1]}")
    if healthy:
        log("HEALTHY: сервис активен, heartbeat свежий")
        return 0

    reason = "; ".join(problems)
    log(f"UNHEALTHY: {reason}")
    if args.dry_run:
        log("dry-run: рестарт не выполняется (код 1 — удобно для cron)")
        return 1

    restart_ok = restart_service(cfg)
    if restart_ok:
        time.sleep(15)  # старт бота + первая метка heartbeat (пишется сразу)
        healthy2, problems2, _ = run_checks(cfg)
        if healthy2:
            log("RECOVERED: после рестарта бот отвечает")
            if (cfg.get("WATCHDOG_NOTIFY") or "1").strip().lower() not in ("0", "false", "no"):
                telegram_notify(
                    cfg.get("BOT_TOKEN") or "", cfg.get("ADMIN_ID") or "",
                    f"🔄 Watchdog: бот не отвечал ({reason}).\n"
                    "Перезапущен автоматически, сейчас отвечает.",
                )
            return 0
        reason = f"{reason}; после рестарта: {'; '.join(problems2)}"

    log(f"FAIL: рестарт не помог ({reason}) — требуется вмешательство")
    if (cfg.get("WATCHDOG_NOTIFY") or "1").strip().lower() not in ("0", "false", "no"):
        telegram_notify(
            cfg.get("BOT_TOKEN") or "", cfg.get("ADMIN_ID") or "",
            f"❌ Watchdog: бот не отвечает ({reason}).\n"
            + ("Рестарт не помог — требуется вмешательство "
               "(journalctl -u fpvbot -n 100)." if restart_ok else
               "Рестарт выполнить не удалось — требуется вмешательство."),
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
