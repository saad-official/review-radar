"""Text encoding helpers: one place that decides how bytes become text.

Review text crosses three byte boundaries (the App Store feed, CSV uploads, the fixture
files) and every one of them is decoded as UTF-8 explicitly. Never rely on the platform
default: on Windows `open()` / `Path.read_text()` without `encoding=` use the ANSI code
page (cp1252), which turns the UTF-8 bytes of "’" (E2 80 99) into "â€™" (mojibake).

`fix_mojibake` undoes exactly that mistake and nothing else; `scripts/repair_mojibake.py`
uses it to clean rows that were stored before the decode paths were fixed.
"""

from __future__ import annotations

from typing import Any

REPLACEMENT = "�"


def decode_upload(raw: bytes) -> str:
    """Decode an uploaded text file. UTF-8 (with or without a BOM) is the contract.

    Bytes that are not valid UTF-8 at all are almost always a legacy Windows export (Excel's
    "CSV" format writes the ANSI code page); those are decoded as cp1252 instead of being
    shredded into U+FFFD. Valid UTF-8 never takes the fallback, so this cannot create mojibake.
    """
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def fix_mojibake(value: str, *, max_rounds: int = 2) -> str:
    """Repair text whose UTF-8 bytes were decoded as cp1252 ("Iâ€™m" -> "I’m").

    A value is changed only when re-encoding it as cp1252 and decoding the bytes as UTF-8
    both succeed strictly, the result differs and contains no U+FFFD. Correct text almost
    never passes that test: a real "’" or "é" is not valid UTF-8 once encoded as cp1252, and
    emoji or CJK cannot be encoded as cp1252 at all. Doubly-encoded text is repaired in up
    to `max_rounds` rounds, each meeting the same test.
    """
    current = value
    for _ in range(max_rounds):
        try:
            repaired = current.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if repaired == current or REPLACEMENT in repaired:
            break
        current = repaired
    return current


def fix_mojibake_deep(value: Any) -> Any:
    """`fix_mojibake` applied to every string inside a JSON-like value (dict keys excluded)."""
    if isinstance(value, str):
        return fix_mojibake(value)
    if isinstance(value, list):
        return [fix_mojibake_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: fix_mojibake_deep(v) for k, v in value.items()}
    return value
