"""Heartbeat для серверного watchdog (deploy/watchdog.py): свежая метка времени,
устойчивость к плохому пути, выключение без HEARTBEAT_FILE в .env."""

import asyncio
from datetime import datetime

import bot as bot_mod


def test_write_heartbeat_creates_file_with_iso(tmp_path, monkeypatch):
    target = tmp_path / "hb" / "heartbeat.txt"  # недостающие каталоги создаются
    monkeypatch.setattr(bot_mod, "HEARTBEAT_FILE", str(target))
    bot_mod._write_heartbeat()
    stamp = datetime.fromisoformat(target.read_text(encoding="utf-8"))
    assert stamp.year >= 2026


def test_write_heartbeat_bad_path_does_not_raise(tmp_path, monkeypatch):
    blocker = tmp_path / "occupied.txt"  # это ФАЙЛ, а не каталог → OSError
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(bot_mod, "HEARTBEAT_FILE", str(blocker / "sub" / "heartbeat.txt"))
    bot_mod._write_heartbeat()  # ошибка записи глотается — бот не должен падать


async def test_heartbeat_loop_disabled_without_path(monkeypatch):
    monkeypatch.setattr(bot_mod, "HEARTBEAT_FILE", "")
    await asyncio.wait_for(bot_mod.heartbeat_loop(), timeout=1)  # выходит сразу


async def test_heartbeat_loop_writes_periodically(tmp_path, monkeypatch):
    target = tmp_path / "heartbeat.txt"
    monkeypatch.setattr(bot_mod, "HEARTBEAT_FILE", str(target))
    monkeypatch.setattr(bot_mod, "HEARTBEAT_INTERVAL", 0.01)
    task = asyncio.create_task(bot_mod.heartbeat_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert target.is_file()  # успел записать метку хотя бы раз
