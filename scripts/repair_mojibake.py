"""Repair stored text whose UTF-8 bytes were once decoded as cp1252 ("Iâ€™m" -> "I’m").

    uv run python scripts/repair_mojibake.py            # dry run: counts only, writes nothing
    uv run python scripts/repair_mojibake.py --apply    # write the repairs (one transaction)

Scans review_radar.reviews (title, body), review_radar.themes (title, summary, quotes[].text),
review_radar.proposals (every string in draft, plus reasoning) and review_radar.signals
(quotes). A value is changed only when `review_radar.text.fix_mojibake` says so: re-encoding
it as cp1252 and decoding as UTF-8 both succeed strictly, the result differs and holds no
U+FFFD. Correct text (a real "’", "é", emoji) never passes that test, so the script is safe
to re-run; a second run finds nothing.

Reads DATABASE_URL from `.env` through AppSettings, like the other scripts; never prints it.
Repaired reviews keep their old embedding (a few characters differ; the vector is still a
fair neighbour). Re-embed only if a review was badly garbled.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from review_radar.config import AppSettings
from review_radar.text import fix_mojibake, fix_mojibake_deep

SUSPECT_MARKERS = ("â€", "Ã", "Â", "â„")  # â€ Ã Â â„


def _fix_text(value: Any) -> Any:
    return fix_mojibake(value) if isinstance(value, str) else value


def _fix_list(value: Any) -> Any:
    return [_fix_text(v) for v in value] if isinstance(value, list) else value


@dataclass(frozen=True)
class Column:
    name: str
    fix: Callable[[Any], Any]
    json: bool = False


@dataclass(frozen=True)
class Table:
    name: str
    key: str
    columns: tuple[Column, ...]


TABLES = (
    Table("reviews", "id", (Column("title", _fix_text), Column("body", _fix_text))),
    Table(
        "themes",
        "id",
        (
            Column("title", _fix_text),
            Column("summary", _fix_text),
            Column("quotes", fix_mojibake_deep, json=True),
        ),
    ),
    Table(
        "proposals",
        "id",
        (Column("draft", fix_mojibake_deep, json=True), Column("reasoning", _fix_text)),
    ),
    Table("signals", "review_id", (Column("quotes", _fix_list),)),
)


@dataclass
class Tally:
    rows: int = 0
    rows_repaired: int = 0
    fields_repaired: dict[str, int] = field(default_factory=dict)
    suspect_left: int = 0


def _has_suspect(value: Any) -> bool:
    if isinstance(value, str):
        return any(marker in value for marker in SUSPECT_MARKERS)
    if isinstance(value, list):
        return any(_has_suspect(v) for v in value)
    if isinstance(value, dict):
        return any(_has_suspect(v) for v in value.values())
    return False


def scan(conn: psycopg.Connection, table: Table, *, apply: bool) -> Tally:
    tally = Tally()
    names = ", ".join(c.name for c in table.columns)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"select {table.key}, {names} from review_radar.{table.name}")
        rows = cur.fetchall()
    for row in rows:
        tally.rows += 1
        changes: dict[str, Any] = {}
        for column in table.columns:
            before = row[column.name]
            after = column.fix(before)
            if after != before:
                changes[column.name] = Jsonb(after) if column.json else after
                tally.fields_repaired[column.name] = tally.fields_repaired.get(column.name, 0) + 1
            if _has_suspect(after):
                tally.suspect_left += 1
        if not changes:
            continue
        tally.rows_repaired += 1
        if apply:
            sets = ", ".join(f"{name} = %s" for name in changes)
            with conn.cursor() as cur:
                cur.execute(
                    f"update review_radar.{table.name} set {sets} where {table.key} = %s",
                    (*changes.values(), row[table.key]),
                )
    return tally


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="count only (the default)")
    mode.add_argument("--apply", action="store_true", help="write the repairs")
    args = parser.parse_args()

    settings = AppSettings()
    if settings.database_url is None:
        print("DATABASE_URL is not set (.env or environment); nothing to scan.")
        return 1
    apply = bool(args.apply)
    print(f"mode: {'APPLY' if apply else 'dry run (use --apply to write)'}")
    totals = 0
    with psycopg.connect(settings.database_url.get_secret_value(), prepare_threshold=None) as conn:
        for table in TABLES:
            tally = scan(conn, table, apply=apply)
            totals += tally.rows_repaired
            fields = ", ".join(f"{k}={v}" for k, v in tally.fields_repaired.items()) or "-"
            print(
                f"{table.name:<10} scanned={tally.rows:<5} repaired_rows={tally.rows_repaired:<4} "
                f"fields: {fields}; suspicious values left={tally.suspect_left}"
            )
        if apply:
            conn.commit()
        else:
            conn.rollback()
    verb = "repaired" if apply else "would repair"
    print(f"{verb} {totals} row(s) in total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
