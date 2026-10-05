"""`uv run evals`: score extraction, clustering, trajectories, guardrails and the issue
template from recorded fixtures, and write docs/evals.md.

    uv run evals                       recorded mode: no network, no keys (CI runs this)
    uv run evals --no-write            print the report only
    uv run evals --live                also extract all 200 labelled reviews with the real
                                       model (20 calls) and save evals/fixtures/extractions.live.json
    uv run evals --record-embeddings   embed the 60 cluster-labelled reviews once (1 call) and
                                       save evals/fixtures/embeddings.json

Inputs (evals/fixtures/):
    reviews.json           200 public App Store reviews of the demo app (feed fields only)
    labels.json            hand labels: category, sentiment, has_device_info (all 200);
                           cluster labels (the 60 most recent)
    recorded_run.json      one real agent run over the 30 most recent reviews (smoke_live.py)
    embeddings.json        configured-provider vectors for the 60 cluster-labelled reviews
    extractions.live.json  optional: a full live extraction of the 200
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..agent.cluster import agglomerate
from ..models import theme_kind_for
from .baseline import predict
from .scorers import (
    cluster_scores,
    extraction_accuracy,
    issue_template_completeness,
    reply_guardrail_rate,
    trajectory_rules,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "evals" / "fixtures"
THRESHOLDS = (0.70, 0.74, 0.78, 0.80, 0.82, 0.85, 0.88)
# Hashed bag-of-words vectors are far less similar than semantic ones; same sweep, lower range.
STAND_IN_THRESHOLDS = (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)


def load(name: str) -> Any | None:
    path = FIXTURES / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def signals_to_prediction(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "category": signal["category"],
        "sentiment": signal["sentiment"],
        "has_device_info": bool(signal.get("devices") or signal.get("os_versions")),
    }


# ------------------------------------------------------------------ live helpers


def live_extract(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    from llm_kit import Ledger

    from ..agent.extract import ReviewSignalsBatch, batches, build_message, to_signals
    from ..config import AppSettings
    from ..engine import Engine
    from ..models import Review, Usage
    from ..prompts import load_prompt

    settings = AppSettings()
    engine = Engine.from_settings(settings)
    ledger = Ledger(max_usd=0.10)
    extract_llm, _, _ = engine.llm_builder(ledger)
    prompt = load_prompt("extract")
    objects = [
        Review(
            id=r["store_review_id"],
            app_id="eval",
            store_review_id=r["store_review_id"],
            rating=r["rating"],
            title=r["title"],
            body=r["body"],
        )
        for r in reviews
    ]
    by_id = {r.id: r for r in objects}
    predictions: dict[str, Any] = {}
    for index, ids in enumerate(batches(list(by_id)), start=1):
        message, refs = build_message([by_id[i] for i in ids])
        batch = extract_llm.complete_structured(
            message, ReviewSignalsBatch, system=prompt.text, label=f"extract:b{index}"
        )
        for signal in to_signals(batch, refs, model=extract_llm.model, prompt_version=prompt.id):
            predictions[signal.review_id] = signal.model_dump(mode="json")
        print(f"  batch {index}: {ledger.summary()}", file=sys.stderr)
    usage = Usage.from_ledger(ledger, 0.10).model_dump(mode="json")
    payload = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "prompt_version": prompt.id,
        "models": sorted(set(extract_llm.served_by)),
        "usage": usage,
        "signals": predictions,
    }
    (FIXTURES / "extractions.live.json").write_text(json.dumps(payload, indent=1), "utf-8")
    return payload


def record_embeddings(reviews: dict[str, dict[str, Any]], ids: list[str]) -> None:
    from llm_kit import Ledger

    from ..config import AppSettings
    from ..embeddings import make_embedder

    # The configured provider and dimension (EMBEDDING_PROVIDER / EMBEDDING_DIMENSIONS), so
    # the clustering sweep scores the vectors production actually stores.
    settings = AppSettings()
    embedder = make_embedder(settings, Ledger())
    texts = [
        f"{reviews[i]['title']}. {reviews[i]['body']}"
        if reviews[i]["title"]
        else reviews[i]["body"]
        for i in ids
    ]
    vectors = embedder.embed(texts, label="eval")
    payload = {
        "model": embedder.model,
        "dimensions": embedder.dimensions,
        "recorded_at": datetime.now(UTC).isoformat(),
        "vectors": {i: [round(v, 5) for v in vec] for i, vec in zip(ids, vectors, strict=True)},
    }
    (FIXTURES / "embeddings.json").write_text(json.dumps(payload), "utf-8")
    print(f"recorded {len(ids)} embeddings: {embedder.ledger.summary()}", file=sys.stderr)


# ------------------------------------------------------------------ report


def run_evals(*, live: bool = False) -> dict[str, Any]:
    reviews_doc = load("reviews.json")
    labels_doc = load("labels.json")
    if not reviews_doc or not labels_doc:
        raise SystemExit("evals/fixtures/reviews.json and labels.json are required")
    reviews = {r["store_review_id"]: r for r in reviews_doc["reviews"]}
    labels = labels_doc["labels"]
    gold_clusters = labels_doc["clusters"]
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "labelled": len(labels),
        "label_distribution": dict(Counter(v["category"] for v in labels.values())),
    }

    # 1. extraction
    baseline = {rid: predict(r["title"], r["body"], r["rating"]) for rid, r in reviews.items()}
    extraction = {"baseline_keywords": extraction_accuracy(baseline, labels)}
    recorded = load("recorded_run.json")
    if recorded:
        preds = {rid: signals_to_prediction(s) for rid, s in recorded["signals"].items()}
        extraction["recorded_run"] = {
            **extraction_accuracy(preds, labels),
            "models": recorded["run"].get("models"),
        }
        base_same = {rid: baseline[rid] for rid in preds if rid in baseline}
        extraction["baseline_on_same_reviews"] = extraction_accuracy(base_same, labels)
    live_doc = live_extract(reviews_doc["reviews"]) if live else load("extractions.live.json")
    if live_doc:
        preds = {rid: signals_to_prediction(s) for rid, s in live_doc["signals"].items()}
        extraction["live_full"] = {
            **extraction_accuracy(preds, labels),
            "models": live_doc["models"],
            "usage": {k: live_doc["usage"][k] for k in ("calls", "usd", "total_tokens")},
        }
    report["extraction"] = extraction

    # 2. clustering
    clustering: dict[str, Any] = {}
    embeddings = load("embeddings.json")
    if not embeddings:
        # No recorded Gemini vectors yet: run the same sweep on the offline lexical hashing
        # embedder so the scorer and the threshold logic are still exercised. The numbers
        # are a floor (word overlap only), and the report says so.
        from ..embeddings import HashEmbedder

        texts = {
            rid: (
                f"{reviews[rid]['title']}. {reviews[rid]['body']}"
                if reviews[rid]["title"]
                else reviews[rid]["body"]
            )
            for rid in gold_clusters
        }
        vectors = HashEmbedder().embed(list(texts.values()))
        embeddings = {
            "model": "hash-768 (offline lexical stand-in)",
            "stand_in": True,
            "vectors": dict(zip(texts, vectors, strict=True)),
        }
    if embeddings:
        vectors = embeddings["vectors"]
        ids = [i for i in gold_clusters if i in vectors]
        sweep = []
        for threshold in STAND_IN_THRESHOLDS if embeddings.get("stand_in") else THRESHOLDS:
            flat = [[ids[k] for k in c] for c in agglomerate([vectors[i] for i in ids], threshold)]
            by_kind: list[list[str]] = []
            groups: dict[str, list[str]] = {}
            for i in ids:
                groups.setdefault(theme_kind_for(labels[i]["category"]), []).append(i)
            for members in groups.values():
                by_kind += [
                    [members[k] for k in c]
                    for c in agglomerate([vectors[i] for i in members], threshold)
                ]
            sweep.append(
                {
                    "threshold": threshold,
                    "global": cluster_scores(flat, gold_clusters),
                    "within_kind": cluster_scores(by_kind, gold_clusters),
                }
            )
        clustering["sweep"] = sweep
        clustering["model"] = embeddings["model"]
        clustering["stand_in"] = bool(embeddings.get("stand_in"))
    if recorded and recorded.get("themes"):
        predicted = [t["member_ids"] for t in recorded["themes"]]
        clustering["recorded_run"] = cluster_scores(predicted, gold_clusters)
        clustering["recorded_run"]["themes"] = [
            {"title": t["title"], "kind": t["kind"], "size": len(t["member_ids"])}
            for t in recorded["themes"]
        ]
    report["clustering"] = clustering

    # 3-5. trajectory, guardrails, template (recorded run), plus a scorer self-test.
    if recorded:
        report["trajectory"] = trajectory_rules(recorded)
        report["guardrails"] = reply_guardrail_rate(recorded, recorded.get("policy", ""))
        report["issue_template"] = issue_template_completeness(recorded)
        report["cost"] = cost_section(recorded)
        report["scorer_self_test"] = self_test(recorded)
    return report


def self_test(recorded: dict[str, Any]) -> dict[str, Any]:
    """A deliberately bad trajectory must fail every rule; otherwise the scorer is broken."""
    bad = json.loads(json.dumps(recorded))
    bad["steps"].append({"kind": "model", "name": "agent", "stage": "propose"})
    bad["steps"].append({"kind": "tool_call", "name": "create_github_issue", "stage": "propose"})
    bad["proposals"].append(
        {
            "id": "x",
            "kind": "issue",
            "status": "executed",
            "draft": {"evidence": [{"review_id": "nope"}]},
        }
    )
    bad["run"]["usage"] = {"usd": 1.0}
    bad["run"]["budget"] = {"max_usd": 0.1, "max_iterations": 0}
    rules = trajectory_rules(bad)["rules"]
    return {"all_rules_fail_on_bad_trajectory": not any(rules.values()), "rules": rules}


def cost_section(recorded: dict[str, Any]) -> dict[str, Any]:
    usage = recorded["run"]["usage"]
    reviews = max(1, len(recorded.get("signals") or {}))
    per_review = usage["usd"] / reviews
    return {
        "reviews": reviews,
        "calls": usage["calls"],
        "tokens": usage["total_tokens"],
        "status": recorded["run"]["status"],
        "error": recorded["run"].get("error"),
        "usd": usage["usd"],
        "usd_per_50_reviews": round(per_review * 50, 6),
        "usd_per_month_daily_50": round(per_review * 50 * 30, 4),
        "by_model": usage.get("by_model", []),
        "seconds": recorded["run"].get("seconds"),
    }


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render(report: dict[str, Any]) -> str:
    out = [
        "# Evals",
        "",
        f"Generated by `uv run evals` on {report['generated_at']} from the recorded fixtures in "
        "`evals/fixtures/` (no network, no keys; CI runs the same command). Numbers below are "
        "regenerated, not hand-edited.",
        "",
        "## The labelled set",
        "",
        f"{report['labelled']} public App Store reviews of the demo app (Spotify, US), the 200 most "
        "recent on 2026-10-04, fetched once from Apple's public feed (store id, rating, title, "
        "body, version, date; no author names). Hand-labelled by the project author for "
        "category, sentiment and has-device-info; the 60 most recent also carry a cluster label. "
        "Category distribution: "
        + ", ".join(
            f"{k} {v}" for k, v in sorted(report["label_distribution"].items(), key=lambda p: -p[1])
        )
        + ".",
        "",
        "### Labelling guide",
        "",
        "- **category**, the most actionable reading for the team: bug > performance > billing > "
        "request > praise > other. *billing* is monetisation: subscriptions, prices, charges, and "
        "complaints about ads or free-tier limits (limited skips, forced shuffle). A review that "
        "reports songs it did not choose without blaming the free tier is labelled as the user "
        "framed it (bug). Generic five-star praise is *praise*; off-topic or unintelligible is *other*.",
        "- **sentiment**: positive, negative, neutral, or *mixed* when clear praise and a clear "
        'complaint both appear. Text wins over the star rating (a 1-star "I love Spotify" is positive).',
        "- **has_device_info**: names a device or platform (iPhone, iPad, AirPods, PlayStation) or "
        'an OS version. "My phone" does not count.',
        "- **clusters** (60): ads, free_tier, not_working, praise, ai_content, misc. *misc* is "
        "excluded from completeness and coverage.",
        "",
        "Single-annotator labels: treat differences of a few points as noise. The hardest calls "
        "(ads vs free tier, praise-with-a-complaint) are where the model and the labels disagree most.",
        "",
        "## Extraction accuracy (per field)",
        "",
        "| Predictor | Reviews | Category | Sentiment | Sentiment (lenient) | Has device info |",
        "|---|---|---|---|---|---|",
    ]
    names = {
        "recorded_run": "Model, recorded live run",
        "baseline_on_same_reviews": "Keyword baseline, same reviews",
        "live_full": "Model, live extraction of all 200",
        "baseline_keywords": "Keyword baseline, all 200",
    }
    for key in ("recorded_run", "baseline_on_same_reviews", "live_full", "baseline_keywords"):
        row = report["extraction"].get(key)
        if row:
            out.append(
                f"| {names[key]} | {row['n']} | {pct(row['category'])} | {pct(row['sentiment'])} | "
                f"{pct(row['sentiment_lenient'])} | {pct(row['has_device_info'])} |"
            )
    model_row = report["extraction"].get("recorded_run") or report["extraction"].get("live_full")
    if model_row:
        out += ["", "Category confusion for the model (rows: label, columns: prediction):", ""]
        cats = ["bug", "performance", "billing", "request", "praise", "other"]
        out.append("| label \\ predicted | " + " | ".join(cats) + " |")
        out.append("|---" * (len(cats) + 1) + "|")
        for gold, row in model_row["confusion"].items():
            out.append(f"| {gold} | " + " | ".join(str(row.get(c, 0)) for c in cats) + " |")
    if not report["extraction"].get("live_full"):
        out += [
            "",
            "The full 200-review live extraction (`uv run evals --live`, 20 model calls) has not "
            "been recorded yet: the model rows cover the reviews of the recorded run only.",
        ]

    out += ["", "## Clustering", ""]
    clustering = report["clustering"]
    if clustering.get("stand_in"):
        out += [
            "**Stand-in vectors.** The Gemini vectors for these 60 reviews are not recorded yet "
            "(the live smoke hit the embeddings quota; `uv run evals --record-embeddings` records "
            "them with one call). The sweep below uses the offline lexical hashing embedder, so it "
            "measures word overlap, not meaning: read it as a floor and as proof the scorer works.",
            "",
        ]
    if clustering.get("sweep"):
        out += [
            f"Average-linkage agglomerative clustering over `{clustering['model']}` vectors "
            "(768-d) of the 60 cluster-labelled reviews, swept over the merge threshold. *global* "
            "clusters everything together; *within kind* first partitions by theme kind (from the "
            "hand labels here, from the model's category in a real run), which is what "
            "`routing.toml` does by default.",
            "",
            "| Threshold | Global purity | Global completeness | Global coverage | Within-kind purity | Within-kind completeness | Within-kind coverage |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in clustering["sweep"]:
            g, k = row["global"], row["within_kind"]
            out.append(
                f"| {row['threshold']:.2f} | {pct(g['purity'])} | {pct(g['completeness'])} | "
                f"{pct(g['coverage'])} | {pct(k['purity'])} | {pct(k['completeness'])} | "
                f"{pct(k['coverage'])} |"
            )
    else:
        out.append("No recorded embeddings yet (`uv run evals --record-embeddings`).")
    if clustering.get("recorded_run"):
        rr = clustering["recorded_run"]
        out += [
            "",
            f"Themes created by the recorded live run: {rr['clusters']} clusters, purity "
            f"{pct(rr['purity'])}, completeness {pct(rr['completeness'])}, coverage {pct(rr['coverage'])}.",
            "",
        ]
        for theme in rr["themes"]:
            out.append(f"- {theme['kind']}: “{theme['title']}” ({theme['size']} reviews)")

    if report.get("trajectory"):
        t = report["trajectory"]
        out += [
            "",
            "## Trajectory rules (recorded run)",
            "",
            "| Rule | Result |",
            "|---|---|",
        ]
        for rule, ok in t["rules"].items():
            out.append(f"| {rule.replace('_', ' ')} | {'pass' if ok else 'FAIL'} |")
        out += [
            "",
            f"{t['model_steps']} model turns (cap {t['cap']}), {t['tool_calls']} tool calls, "
            f"${t['usd']:.6f} of a ${t['max_usd']:.2f} budget. Scorer self-test (a deliberately "
            "unsafe trajectory must fail every rule): "
            + (
                "pass."
                if report["scorer_self_test"]["all_rules_fail_on_bad_trajectory"]
                else "FAIL."
            ),
        ]
        g = report["guardrails"]
        i = report["issue_template"]
        out += [
            "",
            "## Reply guardrails and issue template (recorded run)",
            "",
            f"- Reply drafts checked: {g['attempts']}; first-attempt pass rate "
            f"**{pct(g['first_attempt_pass_rate'])}**; stored replies {g['stored_replies']} "
            f"(re-checked pass rate {pct(g['stored_pass_rate'])}, mean length {g['mean_length']} characters).",
            f"- Issue proposals: {i['issues']}; template completeness **{pct(i['completeness'])}**; "
            f"mean evidence quotes {i['mean_evidence_quotes']}.",
        ]
        c = report["cost"]
        if c["status"] != "done":
            out += [
                "",
                f"**The recorded run is partial** (status `{c['status']}`): "
                f"`{' '.join((c['error'] or '').split())[:120]}...`. It covers fetch and extraction of 30 reviews; the "
                "embed, cluster and propose stages were not reached, so the guardrail and template "
                "rows above are empty until a complete run is recorded (`scripts/smoke_live.py`).",
            ]
        out += [
            "",
            "## Cost per run (recorded run)",
            "",
            (
                "Extraction stage only (the run stopped at embeddings): "
                if c["status"] != "done"
                else ""
            )
            + f"{c['reviews']} reviews, {c['calls']} calls, {c['tokens']} tokens, **${c['usd']:.6f}** "
            f"at paid rates; ${c['usd_per_50_reviews']:.5f} per 50 reviews, about "
            f"${c['usd_per_month_daily_50']:.3f} per app per month at 50 new reviews a day "
            "(target: under $1). Details in docs/costs.md.",
        ]
    out += [
        "",
        "## Not measured here",
        "",
        "- The LLM-judge rubric for reply tone (spec section 7) is not run: today's model quota "
        "went to the live run. The guardrail checks above are deterministic and always run.",
        "- Prompt-injection containment is scored by the test suite rather than here "
        "(`tests/test_guardrails.py`): an agent that obeys any instruction it can see, and guesses "
        "ids it was never given, produces no issue proposal from the injection fixture review.",
    ]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Review Radar evals")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--record-embeddings", action="store_true")
    parser.add_argument("--recorded", action="store_true", help="the default; accepted for CI")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.record_embeddings:
        reviews = {r["store_review_id"]: r for r in load("reviews.json")["reviews"]}
        record_embeddings(reviews, list(load("labels.json")["clusters"]))
    report = run_evals(live=args.live)
    text = render(report)
    if args.no_write:
        print(text)
    else:
        (ROOT / "docs" / "evals.md").write_text(text, encoding="utf-8")
        (FIXTURES.parent / "last_report.json").write_text(json.dumps(report, indent=1), "utf-8")
        print(text)
        print("wrote docs/evals.md", file=sys.stderr)


if __name__ == "__main__":
    main()
