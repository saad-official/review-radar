"""One real run, end to end: real models, real embeddings, the configured database. Manual only.

    uv run python scripts/smoke_live.py                 # 30 reviews, Groq then Gemini
    uv run python scripts/smoke_live.py --no-groq       # skip Groq (its daily quota is spent)
    uv run python scripts/smoke_live.py --max-reviews 10

What it does:
  1. registers the demo app (demo.toml) if needed and seeds the 200 labelled fixture reviews
     (public feed data, no model calls); the feed fetch stage is disabled for this run so the
     working set is exactly the 30 most recent *labelled* reviews;
  2. runs the agent once (extract -> embed -> cluster -> propose) with the real ledger;
  3. writes runs/smoke-<timestamp>/ (summary, ledger) and evals/fixtures/recorded_run.json,
     the recorded run that `uv run evals` scores offline.

If a provider answers with a quota error the run fails with `model_quota_exhausted`; this
script reports it and stops. It never retries (quota is shared with other projects).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from review_radar.config import AppSettings
from review_radar.demo import ensure_demo_app, fixture_reviews, run_agent
from review_radar.engine import Engine
from review_radar.evals.record import export_run
from review_radar.service import import_reviews

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-reviews", type=int, default=30)
    parser.add_argument("--no-groq", action="store_true")
    args = parser.parse_args()

    settings = AppSettings()
    if args.no_groq:
        settings = settings.model_copy(update={"groq_api_key": None})
    engine = Engine.from_settings(settings)
    app = ensure_demo_app(engine)
    seeded = import_reviews(engine, app, fixture_reviews(200))
    counts = engine.store.app_counts(app.id)
    print(f"app {app.name}: seeded {len(seeded)} new fixture reviews; {counts}")

    result = run_agent(engine, app, max_reviews=args.max_reviews, fetch=False, trigger="demo")
    print(json.dumps({k: result[k] for k in ("status", "error", "seconds", "stats")}, indent=2))
    usage = result["usage"] or {}
    print(f"ledger: {usage.get('summary')}")

    recorded = export_run(engine, result["run_id"], result["seconds"])
    out = ROOT / "runs" / f"smoke-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(result, indent=2, default=str), "utf-8")
    (out / "recorded_run.json").write_text(json.dumps(recorded, indent=1), "utf-8")
    if result["status"] != "done":
        print(f"run did not finish: {result['error']}", file=sys.stderr)
        raise SystemExit(1)
    fixture = ROOT / "evals" / "fixtures" / "recorded_run.json"
    fixture.write_text(json.dumps(recorded, indent=1), "utf-8")
    print(f"wrote {out} and {fixture}")


if __name__ == "__main__":
    main()
