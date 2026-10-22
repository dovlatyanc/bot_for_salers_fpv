"""Разовый фикс: строка 8S8P в data/bot.db (прайс был битый, cost 7700 → 17800)."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db
from db import repositories as _repos
from db import prices


async def main() -> None:
    db.init_engine()  # SQLite по умолчанию: data/bot.db
    raw = json.load(open("config/prices.json", encoding="utf-8"))
    row = raw["8S8P"]
    results = {
        f: await prices.set_field("8S8P", f, float(v))
        for f, v in row.items()
    }
    print("set_field:", results)
    print("8S8P in DB:", (await prices.load()).get("8S8P"))
    await db.close_db()


asyncio.run(main())
