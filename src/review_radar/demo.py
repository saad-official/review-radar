"""`uv run demo`: register the public demo app, seed its reviews, optionally run the agent.

    uv run demo                      # seed up to 200 reviews from the App Store feed (no model)
    uv run demo --from-fixture       # seed the 200 labelled eval reviews instead (offline)
    uv run demo --live               # ...then run the agent once (real models, uses quota)
    uv run demo --live --max-reviews 30

Uses Postgres when DATABASE_URL is set, else an in-memory store (then `--live` is the only
way to see anything, and it is printed, not kept). The app is defined in demo.toml.
"""

from __future__ import annotations

import argparse
import json
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AppSettings
from .engine import Engine
from .ingest.base import ReviewIn
from .models import App
from .service import RunService, create_app, import_reviews, seed_from_feed

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "evals" / "fixtures" / "reviews.json"


def load_demo() -> dict[str, Any]:
    return tomllib.loads((ROOT / "demo.toml").read_text(encoding="utf-8"))


def fixture_reviews(limit: int = 200) -> list[ReviewIn]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))["reviews"][:limit]
    return [
        ReviewIn(
            store_review_id=r["store_review_id"],
            source="appstore",
            rating=r["rating"],
            title=r["title"],
            body=r["body"],
            app_version=r["app_version"],
            date=datetime.fromisoformat(r["date"]) if r["date"] else None,
        )
        for r in data
    ]


def ensure_demo_app(engine: Engine) -> App:
    spec = load_demo()["app"]
    existing = engine.store.find_app(spec["store"], spec["store_id"], spec["country"])
    if existing is not None:
        return existing
    return create_app(
        engine,
        store_kind=spec["store"],
        store_id=spec["store_id"],
        name=spec["name"],
        country=spec["country"],
        github_repo=spec.get("github_repo"),
        policy=spec.get("policy", "").strip(),
        public=bool(spec.get("public", True)),
    )


def seed(engine: Engine, app: App, *, from_fixture: bool, limit: int) -> dict[str, Any]:
    if from_fixture:
        new_ids = import_reviews(engine, app, fixture_reviews(limit))
        return {"source": "fixture", "new": len(new_ids)}
    pages = max(1, min(10, (limit + 49) // 50))
    return {"source": "feed", **seed_from_feed(engine, app, max_pages=pages)}


def run_agent(
    engine: Engine, app: App, *, max_reviews: int, fetch: bool = True, trigger: str = "demo"
) -> dict[str, Any]:
    if not fetch:
        engine.source_builder = lambda _app: None
    service = RunService(engine)
    run = service.create_run(app.id, trigger=trigger, max_reviews=max_reviews)
    started = time.monotonic()
    while True:
        outcome = service.process(run.id, engine.settings.process_time_budget_s)
        if not outcome.resumable:
            break
    finished = engine.store.get_run(run.id)
    assert finished is not None
    return {
        "run_id": run.id,
        "status": finished.status,
        "error": finished.error,
        "seconds": round(time.monotonic() - started, 1),
        "stats": finished.stats,
        "usage": finished.usage,
        "summary": finished.summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-fixture", action="store_true")
    parser.add_argument("--live", action="store_true", help="run the agent once (real models)")
    parser.add_argument("--max-reviews", type=int, default=30)
    parser.add_argument("--seed-limit", type=int, default=load_demo()["seed"]["max_reviews"])
    args = parser.parse_args()

    engine = Engine.from_settings(AppSettings())
    app = ensure_demo_app(engine)
    print(f"app {app.name} ({app.id})")
    print("seed:", seed(engine, app, from_fixture=args.from_fixture, limit=args.seed_limit))
    print("counts:", engine.store.app_counts(app.id))
    if args.live:
        result = run_agent(engine, app, max_reviews=args.max_reviews, fetch=not args.from_fixture)
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
