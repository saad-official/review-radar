"""One-time: fetch the 200 most recent public App Store reviews of the demo app into
`evals/fixtures/reviews.json` (then hand-label them in `evals/fixtures/labels.json`).

    uv run python scripts/fetch_eval_reviews.py

Public feed data only: store review id, rating, title, body, app version and date. The
author's display name is deliberately not kept in the fixture (the evals do not need it).
Re-running overwrites the fixture, which invalidates the labels; do it only on purpose.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from review_radar.ingest.appstore import AppStoreSource

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    demo = tomllib.loads((ROOT / "demo.toml").read_text(encoding="utf-8"))["app"]
    result = AppStoreSource().fetch(demo["store_id"], demo["country"], max_pages=4)
    reviews = [
        {
            "store_review_id": r.store_review_id,
            "rating": r.rating,
            "title": r.title,
            "body": r.body,
            "app_version": r.app_version,
            "date": r.date.isoformat() if r.date else None,
        }
        for r in result.reviews[:200]
    ]
    out = ROOT / "evals" / "fixtures" / "reviews.json"
    payload = {
        "source": f"https://itunes.apple.com/{demo['country']}/rss/customerreviews/id={demo['store_id']}",
        "app": demo["name"],
        "fetched_pages": result.pages,
        "reviews": reviews,
    }
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(reviews)} reviews to {out}")


if __name__ == "__main__":
    main()
