#!/usr/bin/env python3
"""Одноразовое снятие webhook с токена бота (перед первым polling-стартом на VPS).

Зачем: если на токене ранее был оставлен активный webhook, polling падает с
TelegramConflictError («terminated by other getUpdate request» / webhook active).
Скрипт вызывает deleteWebhook?drop_pending_updates=true и печатает JSON-ответ.

Запуск из корня проекта:
    python deploy/delete_webhook.py
Ожидаемый ответ: {"ok": true, "result": true, ...}

Ошибки (нет токена в .env, сеть недоступна, Telegram вернул ok:false, 404)
печатаются понятным текстом, токен маскируется, exit-код ненулевой.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import aiohttp
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
TIMEOUT = aiohttp.ClientTimeout(total=15)


def mask_token(token: str) -> str:
    """Токен → '123456789:AA…****' (для сообщений об ошибках/логов)."""
    if not token:
        return "<пусто>"
    head, _, tail = token.partition(":")
    shown = tail[:2] if len(tail) > 4 else ""
    return f"{head}:{shown}…****"


def build_url(token: str) -> str:
    return (
        "https://api.telegram.org/bot"
        + quote(token, safe="")
        + "/deleteWebhook?drop_pending_updates=true"
    )


async def delete_webhook(token: str) -> dict:
    """Вызов Telegram API; возвращает распарсенный JSON-ответ.

    aiohttp.ClientError/asyncio.TimeoutError пробрасываются выше —
    их перехватывает main() с понятным текстом.
    """
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async with session.get(build_url(token)) as resp:
            text = await resp.text()
            if resp.status == 404:
                raise RuntimeError(
                    "Telegram API вернул 404: неверный BOT_TOKEN "
                    f"(токен {mask_token(token)})"
                )
            if resp.status != 200:
                # тело может содержать токен? — нет, но маскируем на всякий случай
                safe = text.replace(token, mask_token(token)) if token else text
                raise RuntimeError(
                    f"Telegram API HTTP {resp.status}: {safe[:300]}"
                )
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"Telegram API вернул не-JSON (HTTP {resp.status}): {text[:300]}"
                )


def main() -> int:
    load_dotenv(BASE_DIR / ".env")
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        print(
            "[delete_webhook] BOT_TOKEN не найден: заполните .env в корне "
            "проекта (BOT_TOKEN=...).",
            file=sys.stderr,
        )
        return 2
    try:
        payload = asyncio.run(delete_webhook(token))
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        print(
            "[delete_webhook] Сетевая ошибка при обращении к Telegram API: "
            f"{type(e).__name__}: {e}. Проверьте интернет/прокси и повторите.",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as e:
        print(f"[delete_webhook] {e}", file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload.get("ok"):
        print("[delete_webhook] OK: webhook снят, отложенные апдейты сброшены "
              "(drop_pending_updates=true). Можно запускать бота (polling).")
        return 0
    # Telegram вернул 200, но ok:false — описание в payload['description']
    print(
        "[delete_webhook] Telegram вернул ok=false: "
        f"{payload.get('description', 'без описания')}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
