"""The ingestion boundary: what every review source produces, and the protocol it implements.

Reviews are public data, and we store only what the source provides: the store's review id,
author name as displayed, rating, title, body, app version and date. Nothing is enriched
(no profile lookups, no cross-referencing). `ReviewIn` is the Pydantic model at this
boundary; anything a source cannot fill is `None`, never guessed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field, field_validator

MAX_BODY_CHARS = 6000


class IngestError(Exception):
    """A source failed in a way the caller should report (code + human message)."""

    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ReviewIn(BaseModel):
    store_review_id: str = Field(min_length=1, max_length=200)
    source: Literal["appstore", "csv", "googleplay"]
    author: str | None = Field(default=None, max_length=200)
    rating: int | None = Field(default=None, ge=1, le=5)
    title: str = Field(default="", max_length=500)
    body: str = Field(min_length=1)
    app_version: str | None = Field(default=None, max_length=50)
    date: datetime | None = None

    @field_validator("body")
    @classmethod
    def _trim_body(cls, value: str) -> str:
        return value.strip()[:MAX_BODY_CHARS]

    @field_validator("title")
    @classmethod
    def _trim_title(cls, value: str) -> str:
        return value.strip()


class FetchResult(BaseModel):
    reviews: list[ReviewIn]
    pages: int = 0
    stopped: str = ""  # why paging stopped: "last_page", "http_400", "empty", "known", ...


class ReviewSource(Protocol):
    """Fetch reviews newest first. `known` lets a source stop paging at the first page that
    contains nothing new (incremental fetch: "new reviews since last run")."""

    name: str

    def fetch(
        self, store_id: str, country: str, *, known: set[str] | None = None, max_pages: int = 10
    ) -> FetchResult: ...


def iter_pages(max_pages: int) -> Iterator[int]:
    yield from range(1, max_pages + 1)


def source_for(store: str, **kwargs) -> ReviewSource:
    if store == "ios":
        from .appstore import AppStoreSource

        return AppStoreSource(**kwargs)
    if store == "android":
        from .googleplay import GooglePlaySource

        return GooglePlaySource()
    raise IngestError("unsupported_store", f"unknown store {store!r}")
