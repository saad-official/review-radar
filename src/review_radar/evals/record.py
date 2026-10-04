"""Export a finished run as the recorded-run fixture that `uv run evals` scores offline.

Internal UUIDs of reviews are replaced by the store's review ids, so the fixture can be
joined with the hand labels (which are keyed by store review id).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..engine import Engine


def export_run(engine: Engine, run_id: str, seconds: float) -> dict[str, Any]:
    store = engine.store
    run = store.get_run(run_id)
    assert run is not None
    app = store.get_app(run.app_id)
    checkpoints = store.get_checkpoints(run_id)
    working = checkpoints.get("working_set") or []
    proposals = store.list_proposals(run.app_id, run_id=run_id)
    theme_ids = list((checkpoints.get("cluster") or {}).get("touched", []))
    members = {tid: store.theme_review_ids(tid) for tid in theme_ids}
    all_ids = list(dict.fromkeys([*working, *(r for m in members.values() for r in m)]))
    reviews = {r.id: r for r in store.get_reviews(all_ids)}
    sid = {rid: r.store_review_id for rid, r in reviews.items()}
    signals = store.get_signals(working)

    def proposal(p) -> dict[str, Any]:
        draft = dict(p.draft)
        if "evidence" in draft:
            draft["evidence"] = [
                {**e, "review_id": sid.get(e["review_id"], e["review_id"])}
                for e in draft["evidence"]
            ]
        return {
            "id": p.id,
            "kind": p.kind,
            "status": p.status,
            "decided_by": p.decided_by,
            "review_id": sid.get(p.review_id or ""),
            "theme_id": p.theme_id,
            "draft": draft,
            "reasoning": p.reasoning,
            "guardrails": p.guardrails,
        }

    themes = []
    for tid in theme_ids:
        theme = store.get_theme(tid)
        if theme:
            themes.append(
                {
                    "id": tid,
                    "title": theme.title,
                    "summary": theme.summary,
                    "kind": theme.kind,
                    "member_ids": [sid[r] for r in members[tid] if r in sid],
                    "quotes": [q.model_dump() for q in theme.quotes],
                }
            )
    models = sorted({row["model"] for row in (run.usage or {}).get("by_model", [])})
    return {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "app": app.name if app else None,
        "policy": app.policy if app else "",
        "run": {
            "id": run.id,
            "status": run.status,
            "error": run.error,
            "summary": run.summary,
            "stats": run.stats,
            "usage": run.usage,
            "budget": run.budget,
            "models": models,
            "seconds": seconds,
        },
        "review_ids": list(sid.values()),
        "signals": {sid[rid]: s.model_dump(mode="json") for rid, s in signals.items()},
        "themes": themes,
        "proposals": [proposal(p) for p in proposals],
        "propose": checkpoints.get("propose"),
        "steps": [s.model_dump(mode="json") for s in store.list_steps(run_id)],
    }
