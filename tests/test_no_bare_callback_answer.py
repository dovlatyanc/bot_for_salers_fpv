"""Этап 11д: голые callback.answer/cb.answer/query.answer в handlers/ запрещены.

Боевой краш: просроченный ack («query is too old») падал необработанным
исключением. Все ack в хендлерах — только через utils.ui.safe_answer
(сам safe_answer в utils/ui.py содержит callback.answer внутри try — там
это разрешено). Строки-комментарии игнорируются.
"""

import re
from pathlib import Path

HANDLERS_DIR = Path(__file__).resolve().parent.parent / "handlers"
BARE_RE = re.compile(r"\b(?:cb|callback|query)\.answer\(")


def test_no_bare_callback_answer_in_handlers():
    offenders = []
    for path in sorted(HANDLERS_DIR.glob("*.py")):
        for i, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            code = line.split("#", 1)[0]
            if "safe_answer" in code:
                continue
            if BARE_RE.search(code):
                offenders.append(f"{path.name}:{i}: {line.strip()}")
    assert not offenders, (
        "голые callback.answer в handlers/ (замените на safe_answer):\n"
        + "\n".join(offenders)
    )
