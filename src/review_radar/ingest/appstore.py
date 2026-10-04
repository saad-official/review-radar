"""App Store reviews from Apple's public customer-reviews RSS feed (no key, no account).

    https://itunes.apple.com/{cc}/rss/customerreviews/page={n}/id={app_id}/sortby=mostrecent/json

Facts about the feed that shaped this code (checked against the live feed, 2026-10-04):

  - 50 reviews per page, pages 1-10, so about 500 recent reviews per country. Page 11 and
    beyond answer **400**, which is the normal end of paging, not an error.
  - `feed.entry` is a list, a single object when a page has one review, and absent when a
    page is empty. Older feeds put an app-metadata entry first (no `im:rating`); entries
    without a rating and content are skipped.
  - The same review can appear on two pages when new reviews arrive mid-fetch (pages
    shift by the number of new reviews), so results are de-duplicated by the review id.
  - The XML flavour carries two `<content>` elements (text and html); we read `type="text"`.

Incremental fetch: with `known` ids, paging stops after the first page whose reviews are
all already stored, which is "new reviews since last run" without trusting dates.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import httpx

from .base import FetchResult, IngestError, ReviewIn

FEED_URL = "https://itunes.apple.com/{cc}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/{fmt}"
ATOM = "{http://www.w3.org/2005/Atom}"
IM = "{http://itunes.apple.com/rss}"


def _label(node: Any) -> str | None:
    if isinstance(node, dict):
        value = node.get("label")
        return str(value) if value is not None else None
    return None


def _date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _review(
    review_id: str | None,
    author: str | None,
    rating: str | None,
    title: str | None,
    body: str | None,
    version: str | None,
    updated: str | None,
) -> ReviewIn | None:
    if not review_id or not body or not body.strip() or not rating:
        return None
    try:
        stars = int(rating)
    except ValueError:
        return None
    if not 1 <= stars <= 5:
        return None
    return ReviewIn(
        store_review_id=review_id,
        source="appstore",
        author=author or None,
        rating=stars,
        title=title or "",
        body=body,
        app_version=version or None,
        date=_date(updated),
    )


def parse_json_page(data: dict[str, Any]) -> list[ReviewIn]:
    entries = (data.get("feed") or {}).get("entry")
    if entries is None:
        return []
    if isinstance(entries, dict):
        entries = [entries]
    reviews = []
    for entry in entries:
        review = _review(
            _label(entry.get("id")),
            _label((entry.get("author") or {}).get("name")),
            _label(entry.get("im:rating")),
            _label(entry.get("title")),
            _label(entry.get("content")),
            _label(entry.get("im:version")),
            _label(entry.get("updated")),
        )
        if review is not None:
            reviews.append(review)
    return reviews


def parse_xml_page(text: str) -> list[ReviewIn]:
    # The Atom feed never declares a DTD. Refusing any document that does closes entity
    # expansion (billion laughs) and external entities without a defusedxml dependency.
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise IngestError("bad_feed", "the feed declared a DTD; refusing to parse it")
    try:
        root = ET.fromstring(text.strip().encode("utf-8"))
    except ET.ParseError as exc:
        raise IngestError("bad_feed", f"the App Store feed was not valid XML: {exc}") from exc
    reviews = []
    for entry in root.findall(f"{ATOM}entry"):
        body = next(
            (
                node.text
                for node in entry.findall(f"{ATOM}content")
                if node.get("type", "text") == "text"
            ),
            None,
        )
        author = entry.find(f"{ATOM}author/{ATOM}name")
        review = _review(
            entry.findtext(f"{ATOM}id"),
            author.text if author is not None else None,
            entry.findtext(f"{IM}rating"),
            entry.findtext(f"{ATOM}title"),
            body,
            entry.findtext(f"{IM}version"),
            entry.findtext(f"{ATOM}updated"),
        )
        if review is not None:
            reviews.append(review)
    return reviews


class AppStoreSource:
    name = "appstore"

    def __init__(self, *, client: httpx.Client | None = None, fmt: str = "json"):
        self._client = client
        self.fmt = fmt

    def _get(self, http: httpx.Client, url: str) -> httpx.Response:
        try:
            return http.get(url, headers={"Accept": "application/json, application/xml"})
        except httpx.HTTPError as exc:
            raise IngestError(
                "feed_unreachable", f"could not reach the App Store feed: {exc}", status=502
            ) from exc

    def fetch(
        self, store_id: str, country: str, *, known: set[str] | None = None, max_pages: int = 10
    ) -> FetchResult:
        if not store_id.isdigit():
            raise IngestError("bad_store_id", "an App Store app id is numeric, e.g. 324684580")
        http = self._client or httpx.Client(timeout=20.0, follow_redirects=True)
        seen: dict[str, ReviewIn] = {}
        pages = 0
        stopped = "last_page"
        try:
            for page in range(1, max(1, min(max_pages, 10)) + 1):
                url = FEED_URL.format(cc=country.lower(), page=page, app_id=store_id, fmt=self.fmt)
                response = self._get(http, url)
                if response.status_code in (400, 404):
                    stopped = f"http_{response.status_code}"
                    break
                if response.status_code >= 400:
                    if page == 1:
                        raise IngestError(
                            "feed_error",
                            f"the App Store feed answered {response.status_code}",
                            status=502,
                        )
                    stopped = f"http_{response.status_code}"
                    break
                pages += 1
                if self.fmt == "json":
                    try:
                        batch = parse_json_page(response.json())
                    except ValueError as exc:
                        raise IngestError("bad_feed", "the feed was not valid JSON") from exc
                else:
                    batch = parse_xml_page(response.text)
                if not batch:
                    stopped = "empty"
                    break
                new = [r for r in batch if r.store_review_id not in seen]
                for review in new:
                    seen[review.store_review_id] = review
                if known is not None and all(r.store_review_id in known for r in batch):
                    stopped = "known"
                    break
        finally:
            if self._client is None:
                http.close()
        return FetchResult(reviews=list(seen.values()), pages=pages, stopped=stopped)


def lookup_app_name(
    store_id: str, country: str, *, client: httpx.Client | None = None
) -> str | None:
    """Best effort: the iTunes Lookup API gives the app's display name (no key needed)."""
    http = client or httpx.Client(timeout=10.0)
    try:
        response = http.get(
            "https://itunes.apple.com/lookup", params={"id": store_id, "country": country}
        )
        results = response.json().get("results") or [] if response.status_code == 200 else []
        return str(results[0].get("trackName")) if results else None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if client is None:
            http.close()
