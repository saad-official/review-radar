"""CSV import: the route in for Google Play (and any store without a feed).

Column schema (header row required, names case-insensitive, extra columns ignored):

    column        required  aliases                                   notes
    body          yes       text, content, review, review text        the review text
    review_id     no        id, store_review_id, review id            the store's id; if absent,
                                                                      a stable id is derived
                                                                      from the row content
    rating        no        stars, star rating, score                 integer 1-5
    title         no        review title, summary
    author        no        user, user name, reviewer
    app_version   no        version, app version, app version name
    date          no        review date, review submit date and time  ISO 8601 or YYYY-MM-DD

The Play Console's "Download reviews" export works as-is through the aliases. Rows that
fail validation are reported with their line number and skipped; they never abort the
import. Re-importing the same file is idempotent (ids are unique per app).
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime

from pydantic import BaseModel, ValidationError

from .base import IngestError, ReviewIn

MAX_ROWS = 5000
MAX_BYTES = 4_000_000

ALIASES: dict[str, tuple[str, ...]] = {
    "body": ("body", "text", "content", "review", "review text"),
    "review_id": ("review_id", "id", "store_review_id", "review id"),
    "rating": ("rating", "stars", "star rating", "score"),
    "title": ("title", "review title", "summary"),
    "author": ("author", "user", "user name", "reviewer"),
    "app_version": ("app_version", "version", "app version", "app version name"),
    "date": ("date", "review date", "review submit date and time"),
}


class RowError(BaseModel):
    line: int
    error: str


class CsvImport(BaseModel):
    reviews: list[ReviewIn]
    errors: list[RowError]
    rows: int


def _column_map(header: list[str]) -> dict[str, int]:
    normalised = [h.strip().lower().replace("_", " ") for h in header]
    mapping: dict[str, int] = {}
    for field, names in ALIASES.items():
        for name in names:
            key = name.replace("_", " ")
            if key in normalised:
                mapping[field] = normalised.index(key)
                break
    return mapping


def _parse_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    for candidate in (value, value.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {value[:40]!r} (use ISO 8601 or YYYY-MM-DD)")


def parse_csv(text: str, *, source: str = "csv") -> CsvImport:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise IngestError("too_large", f"CSV is larger than {MAX_BYTES // 1_000_000} MB")
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise IngestError("empty_csv", "the CSV is empty") from exc
    columns = _column_map(header)
    if "body" not in columns:
        raise IngestError(
            "missing_column",
            "the CSV needs a review text column (body, text, content, review or Review Text)",
        )
    reviews: list[ReviewIn] = []
    errors: list[RowError] = []
    seen: set[str] = set()
    rows = 0
    for line, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        rows += 1
        if rows > MAX_ROWS:
            errors.append(RowError(line=line, error=f"more than {MAX_ROWS} rows; rest skipped"))
            break

        def cell(field: str, row: list[str] = row) -> str:
            index = columns.get(field)
            return row[index].strip() if index is not None and index < len(row) else ""

        try:
            body = cell("body")
            rating_text = cell("rating")
            review_id = (
                cell("review_id")
                or "csv-"
                + hashlib.sha256(f"{cell('author')}|{cell('date')}|{body}".encode()).hexdigest()[
                    :16
                ]
            )
            review = ReviewIn(
                store_review_id=review_id,
                source=source,  # type: ignore[arg-type]
                author=cell("author") or None,
                rating=int(float(rating_text)) if rating_text else None,
                title=cell("title"),
                body=body,
                app_version=cell("app_version") or None,
                date=_parse_date(cell("date")),
            )
        except (ValidationError, ValueError) as exc:
            message = (
                "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            errors.append(RowError(line=line, error=message[:300]))
            continue
        if review.store_review_id in seen:
            continue
        seen.add(review.store_review_id)
        reviews.append(review)
    return CsvImport(reviews=reviews, errors=errors[:100], rows=rows)
