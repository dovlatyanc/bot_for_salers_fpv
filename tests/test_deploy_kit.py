"""deploy-кит (Этап 12а): delete_webhook.py, состав релиза, .env.example.

Лёгкие проверки без сети: HTTP в delete_webhook не дёргается, релиз
собирается на tmp-дереве (не на живом проекте).
"""

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------
# deploy/delete_webhook.py
# ---------------------------------------------------------------

@pytest.fixture(scope="module")
def dw():
    return _load("delete_webhook", ROOT / "deploy" / "delete_webhook.py")


def test_delete_webhook_has_main(dw):
    assert callable(dw.main)


def test_delete_webhook_url_contains_drop_pending(dw):
    url = dw.build_url("123456:AA Example Tok en")
    assert url.startswith("https://api.telegram.org/bot")
    assert "/deleteWebhook" in url
    assert "drop_pending_updates=true" in url


def test_mask_token(dw):
    assert dw.mask_token("") == "<пусто>"
    masked = dw.mask_token("123456789:AAExampleToken")
    assert "123456789" in masked
    assert "AAExampleToken" not in masked  # секретная часть не светится
    assert "****" in masked


def test_delete_webhook_404_raises_readable(dw):
    import aiohttp

    def fake_get(url):
        class Resp:
            status = 404

            async def text(self):
                return ""

        class Ctx:
            async def __aenter__(self):
                return Resp()

            async def __aexit__(self, *a):
                return False

        return Ctx()

    class FakeSession:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url):
            return fake_get(url)

    orig = aiohttp.ClientSession
    aiohttp.ClientSession = FakeSession
    try:
        with pytest.raises(RuntimeError) as ei:
            import asyncio
            asyncio.run(dw.delete_webhook("123456789:AAExampleSecretToken"))
    finally:
        aiohttp.ClientSession = orig
    assert "404" in str(ei.value)
    assert "AAExampleSecretToken" not in str(ei.value)  # токен замаскирован


# ---------------------------------------------------------------
# deploy/make_release.py — состав архива на tmp-дереве
# ---------------------------------------------------------------

def _make_tree(base: Path) -> None:
    for rel in (
        "bot.py", "config.py", "requirements.txt", ".env.example",
        "deploy/make_release.py", "deploy/fpvbot.service",
        "docs/DEPLOY.md",
        "data/builder/parts.json", "data/bot.db", "data/bot.db-wal",
        "data/backups/bot_old.db",
        "services/ai_providers/__init__.py",
        "keyboards/menu.py",
        ".env", "bot_system.log",
        "logs/run.log", ".venv/lib/x.py", "__pycache__/x.pyc",
        "node_modules/pkg/index.js", "dist/old.zip",
        "config/calc_history.json",
    ):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")


def test_release_archive_composition(tmp_path):
    mr = _load("make_release", ROOT / "deploy" / "make_release.py")
    tree = tmp_path / "proj"
    _make_tree(tree)
    target = tmp_path / "out.zip"

    names = mr.build_archive(tree, target)
    assert target.exists()

    must = {
        "bot.py", "requirements.txt",
        "deploy/make_release.py", "docs/DEPLOY.md",
        "data/builder/parts.json",
        "services/ai_providers/__init__.py",
        "keyboards/menu.py",
    }
    assert must <= set(names)

    forbidden_exact = {".env", "data/bot.db", "data/bot.db-wal",
                       "data/backups/bot_old.db"}
    forbidden_markers = (
        "data/backups/", "logs/", ".venv/", "__pycache__", "node_modules/",
        "dist/", "calc_history.json",
    )
    for entry in names:
        assert entry not in forbidden_exact, f"лишнее в архиве: {entry}"
        assert not entry.endswith((".log", ".pyc")), f"лишнее в архиве: {entry}"
        for marker in forbidden_markers:
            assert marker not in entry, f"лишнее в архиве: {entry}"


def test_release_archive_rejects_forbidden(tmp_path):
    """Страховочная защита: если exclude-списки ослабить — сборка падает."""
    mr = _load("make_release", ROOT / "deploy" / "make_release.py")
    tree = tmp_path / "proj"
    _make_tree(tree)
    # имитируем дыру: файл .env на время исключаем из списков
    orig_names = set(mr.EXCLUDE_NAMES)
    mr.EXCLUDE_NAMES.discard(".env")
    try:
        with pytest.raises(RuntimeError, match="запрещённые файлы"):
            mr.build_archive(tree, tmp_path / "bad.zip")
    finally:
        mr.EXCLUDE_NAMES.clear()
        mr.EXCLUDE_NAMES.update(orig_names)


# ---------------------------------------------------------------
# .env.example — полнота (Шаг 3)
# ---------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "BOT_TOKEN", "ADMIN_ID", "DB_URL", "HEARTBEAT_FILE",
    "HEARTBEAT_INTERVAL", "PROXY_URL", "AI_PROVIDER",
    "AI_PROXY_URL", "AI_PROXY_TOKEN", "YANDEX_API_KEY", "YANDEX_FOLDER_ID",
])
def test_env_example_has_keys(key):
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert key in text, f"в .env.example нет {key}"


def test_env_example_proxy_examples():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "socks5://" in text
    assert "http://" in text


def test_env_example_no_real_secrets():
    """Нет значений-секретов: плейсхолдеры — ок, длинные случайные токены — нет."""
    import re
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        value = line.split("=", 1)[1].strip()
        assert not re.fullmatch(r"\d{8,}:[A-Za-z0-9_-]{30,}", value), \
            f"похоже на настоящий токен: {line}"
