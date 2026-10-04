"""Google Play: deliberately not implemented in the Python API.

Google Play has no public review feed. The spec's options are a tiny Node scraper run in
GitHub Actions (optional, not in this MVP) or CSV import, which is what this stub points to.
The stub exists so `store: android` apps are first-class (themes, proposals, runs all work)
and the fetch stage reports a clear, actionable error instead of failing obscurely.
"""

from __future__ import annotations

from .base import FetchResult, IngestError


class GooglePlaySource:
    name = "googleplay"

    def fetch(
        self, store_id: str, country: str, *, known: set[str] | None = None, max_pages: int = 10
    ) -> FetchResult:
        raise IngestError(
            "not_supported",
            "Google Play reviews cannot be fetched by the API (there is no public feed). "
            "Export them (Play Console > Reviews > Download, or any scraper) and import the "
            "CSV with POST /api/apps/{id}/import; see docs/api.md for the column schema.",
            status=422,
        )
