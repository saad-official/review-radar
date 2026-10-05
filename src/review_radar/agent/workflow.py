"""The agent run: a deterministic workflow with one bounded judgement loop (decision 0001).

    fetch -> extract -> embed -> cluster -> propose -> finish

Every stage is checkpointed, so a `/process` request that hits its time budget raises
`RunSuspended` and the next request resumes where this one stopped:

  - fetch      checkpoint `fetch` + `working_set` (the review ids this run owns)
  - extract    the `signals` table is the checkpoint: a review with signals is done
  - embed      `reviews.embedding` is the checkpoint
  - cluster    checkpoint `cluster` (themes created or touched)
  - propose    checkpoint `propose` (the loop's outcome); the loop is only started with at
               least `min_window_s` left, and its own deadline is the smaller of 200 s and
               what remains of the request
  - ledger     checkpoint `ledger` after every model call, so a resumed run keeps its spend
               and the $0.10 ceiling covers the whole run, not each request

Only `propose` is agentic. The model decides *what* to propose and *how to phrase it*;
the workflow decides *that* stages run, in which order, within which budgets, and the
tools decide what a proposal may contain. Nothing here can approve or execute anything.

Every stage and every model turn, tool call and tool result is appended to `run_steps`:
the trajectory the UI replays and the trajectory evals score.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from llm_kit import CallRecord, Ledger, LLMBudgetError, LLMError
from llm_kit import Usage as LLMUsage

from ..db.store import Store
from ..embeddings import EmbeddingProvider
from ..ingest.base import IngestError, ReviewSource
from ..llm import LLMRouteError
from ..models import App, Review, Run, Theme, ThemeQuote, theme_kind_for
from ..prompts import load_prompt, wrap_untrusted
from ..routing import Routing
from .cluster import (
    ThemeNameBatch,
    agglomerate,
    build_naming_message,
    centroid,
    fallback_name,
    updated_centroid,
    verified_quotes,
)
from .extract import (
    ReviewSignalsBatch,
    batches,
    build_message,
    quarantine_signals,
    split_quarantined,
    to_signals,
)
from .injection import FLAG
from .tools import ToolContext, build_tools

log = logging.getLogger(__name__)

MAX_THEMES_IN_CONTEXT = 12
MAX_MEMBERS_PER_THEME = 6
MAX_REPLY_CANDIDATES = 15
MAX_CLUSTERS_NAMED = 12
SUSPEND_MARGIN_S = 20.0
STAGE_OF = {
    "fetching": "fetch",
    "extracting": "extract",
    "embedding": "embed",
    "clustering": "cluster",
    "proposing": "propose",
}


class RunSuspended(Exception):
    """The request's time budget ran out between stages. Not a failure: resume later."""


def ledger_to_json(ledger: Ledger) -> list[dict[str, Any]]:
    return [record.as_dict() for record in ledger.records]


def ledger_from_json(rows: list[dict[str, Any]] | None, max_usd: float | None) -> Ledger:
    """Rebuild a Ledger from checkpointed records so a resumed run keeps its spend."""
    ledger = Ledger(max_usd=max_usd)
    for row in rows or []:
        ledger.add(
            CallRecord(
                provider=row["provider"],
                model=row["model"],
                label=row["label"],
                usage=LLMUsage(
                    row.get("prompt_tokens", 0),
                    row.get("completion_tokens", 0),
                    row.get("reasoning_tokens", 0),
                ),
                latency_s=row.get("latency_s", 0.0),
                finish_reason=row.get("finish_reason"),
                attempts=row.get("attempts", 1),
                error=row.get("error"),
                started_at=row.get("started_at", ""),
            )
        )
    return ledger


def usage_since(ledger: Ledger, before: int) -> dict[str, Any] | None:
    records = ledger.records[before:]
    if not records:
        return None
    return {
        "calls": len(records),
        "model": records[-1].model,
        "prompt_tokens": sum(r.usage.prompt_tokens for r in records),
        "completion_tokens": sum(r.usage.completion_tokens for r in records),
        "reasoning_tokens": sum(r.usage.reasoning_tokens for r in records),
        "usd": round(sum(r.cost_usd for r in records), 8),
        "latency_s": round(sum(r.latency_s for r in records), 3),
        "errors": [r.error for r in records if r.error][:3] or None,
    }


class Checkpoints:
    def __init__(self, store: Store, run_id: str):
        self.store = store
        self.run_id = run_id
        self._cache = store.get_checkpoints(run_id)

    def get(self, key: str) -> Any:
        return self._cache.get(key)

    def put(self, key: str, data: Any) -> None:
        self._cache[key] = data
        self.store.put_checkpoint(self.run_id, key, data)


@dataclass
class WorkflowResult:
    summary: str
    stats: dict[str, Any]
    created_proposals: list[str] = field(default_factory=list)


@dataclass
class RunWorkflow:
    store: Store
    routing: Routing
    app: App
    run: Run
    ledger: Ledger
    extract_llm: Any  # StructuredLLM
    theme_llm: Any  # StructuredLLM
    agent_llm: Any  # ToolsLLM
    embedder: EmbeddingProvider | None
    source: ReviewSource | None
    deadline: float  # time.monotonic() value
    max_reviews: int = 50
    feed_max_pages: int = 10
    max_replies: int = 10
    max_issues: int = 5
    on_status: Callable[[str], None] = lambda status: None
    clock: Callable[[], float] = time.monotonic
    checkpoints: Checkpoints | None = None
    budget_hit: bool = False

    def __post_init__(self) -> None:
        if self.checkpoints is None:
            self.checkpoints = Checkpoints(self.store, self.run.id)
        self.prompt_versions = {
            name: load_prompt(name, version).id for name, version in self.routing.prompts.items()
        }

    # ------------------------------------------------------------------ helpers

    @property
    def cp(self) -> Checkpoints:
        assert self.checkpoints is not None
        return self.checkpoints

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def check_time(self, stage: str) -> None:
        if self.remaining() < SUSPEND_MARGIN_S:
            self.step(stage, "note", "suspended", result={"remaining_s": round(self.remaining())})
            raise RunSuspended(f"time budget reached during {stage}")

    def step(
        self,
        stage: str,
        kind: str,
        name: str,
        args: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.store.append_step(self.run.id, stage, kind, name, args, result, usage)

    def save_ledger(self) -> None:
        self.cp.put("ledger", ledger_to_json(self.ledger))

    def status(self, status: str) -> None:
        self.on_status(status)
        self.step(STAGE_OF.get(status, status), "note", "status", result={"status": status})

    # ------------------------------------------------------------------ run

    def execute(self) -> WorkflowResult:
        self.fetch()
        working = self.cp.get("working_set") or []
        self.extract(working)
        self.embed(working)
        self.cluster(working)
        outcome = self.propose(working)
        return self.finish(working, outcome)

    # ------------------------------------------------------------------ fetch

    def fetch(self) -> None:
        if self.cp.get("fetch") is not None:
            return
        self.status("fetching")
        args = {"store": self.app.store, "app": self.app.store_id, "country": self.app.country}
        self.step("fetch", "tool_call", "fetch_reviews", args=args)
        result: dict[str, Any]
        if self.source is None:
            result = {"skipped": True, "reason": "no source configured"}
        else:
            known = self.store.known_review_ids(self.app.id)
            try:
                fetched = self.source.fetch(
                    self.app.store_id,
                    self.app.country,
                    known=known,
                    max_pages=self.feed_max_pages,
                )
                new_ids = self.store.upsert_reviews(self.app.id, fetched.reviews)
                result = {
                    "fetched": len(fetched.reviews),
                    "new": len(new_ids),
                    "pages": fetched.pages,
                    "stopped": fetched.stopped,
                }
            except IngestError as exc:
                # Not fatal: imported or previously fetched reviews can still be triaged.
                result = {"error": exc.code, "message": exc.message}
        working = self.store.unprocessed_review_ids(self.app.id, self.max_reviews)
        result["working_set"] = len(working)
        self.step("fetch", "tool_result", "fetch_reviews", result=result)
        self.cp.put("working_set", working)
        self.cp.put("fetch", result)

    # ------------------------------------------------------------------ extract

    def extract(self, working: list[str]) -> None:
        done = self.store.get_signals(working)
        todo_ids = [rid for rid in working if rid not in done]
        if not todo_ids:
            return
        self.status("extracting")
        reviews = self.store.get_reviews(todo_ids)
        clean, quarantined = split_quarantined(reviews)
        if quarantined:
            self.store.put_signals([quarantine_signals(r) for r in quarantined])
            self.step(
                "extract",
                "note",
                "quarantine",
                result={
                    "reviews": [r.id for r in quarantined],
                    "flag": FLAG,
                    "why": "instruction-like text; never sent to a model, never evidence",
                },
            )
        prompt = load_prompt("extract", self.routing.prompts.get("extract", "v1"))
        by_id = {r.id: r for r in clean}
        for index, batch_ids in enumerate(batches([r.id for r in clean]), start=1):
            if self.budget_hit:
                return
            self.check_time("extract")
            batch = [by_id[rid] for rid in batch_ids]
            message, refs = build_message(batch)
            self.step(
                "extract",
                "tool_call",
                "extract_signals",
                args={"batch": index, "review_ids": batch_ids},
            )
            before = len(self.ledger.records)
            try:
                parsed = self.extract_llm.complete_structured(
                    message, ReviewSignalsBatch, system=prompt.text, label=f"extract:b{index}"
                )
            except LLMBudgetError as exc:
                self.budget_hit = True
                self.step("extract", "note", "budget", result={"message": str(exc)})
                self.save_ledger()
                return
            finally:
                self.save_ledger()
            model = getattr(self.extract_llm, "model", "unknown")
            signals = to_signals(parsed, refs, model=model, prompt_version=prompt.id)
            self.store.put_signals(signals)
            self.step(
                "extract",
                "tool_result",
                "extract_signals",
                result={
                    "extracted": sum(1 for s in signals if not s.flags),
                    "missing": sum(1 for s in signals if s.flags),
                    "categories": dict(Counter(s.category for s in signals)),
                    "model": model,
                },
                usage=usage_since(self.ledger, before),
            )

    # ------------------------------------------------------------------ embed

    def _clusterable(self, working: list[str]) -> list[Review]:
        signals = self.store.get_signals(working)
        return [
            r
            for r in self.store.get_reviews(working)
            if r.id in signals and FLAG not in signals[r.id].flags
        ]

    def embed(self, working: list[str]) -> None:
        if self.embedder is None:
            if self.cp.get("embed_skipped") is None:
                self.step("embed", "note", "skipped", result={"reason": "no embedding provider"})
                self.cp.put("embed_skipped", True)
            return
        todo = [r for r in self._clusterable(working) if r.embedding is None]
        if not todo or self.budget_hit:
            return
        self.status("embedding")
        self.check_time("embed")
        self.step(
            "embed",
            "tool_call",
            "embed_reviews",
            args={"count": len(todo), "model": self.embedder.model},
        )
        before = len(self.ledger.records)
        try:
            vectors = self.embedder.embed([r.text for r in todo], label="embed")
        except LLMBudgetError as exc:
            self.budget_hit = True
            self.step("embed", "note", "budget", result={"message": str(exc)})
            return
        except LLMError as exc:
            # Same contract as the chat tiers: a provider failure ends the run with a
            # classified error (quota vs unavailable); the signals already stored are kept
            # and the next run embeds these reviews (they stay in its working set).
            self.step("embed", "tool_result", "embed_reviews", result={"error": str(exc)[:600]})
            raise LLMRouteError("embed", [f"{self.embedder.model}: {exc}"]) from exc
        finally:
            self.save_ledger()
        self.store.set_embeddings({r.id: v for r, v in zip(todo, vectors, strict=True)})
        restored = self._restore_theme_centroids([r.id for r in todo])
        result: dict[str, Any] = {
            "embedded": len(vectors),
            "dimensions": len(vectors[0]) if vectors else 0,
        }
        if restored:
            result["centroids_restored"] = restored
        self.step(
            "embed",
            "tool_result",
            "embed_reviews",
            result=result,
            usage=usage_since(self.ledger, before),
        )

    def _restore_theme_centroids(self, review_ids: list[str]) -> int:
        """Give a centroid back to themes that lost it.

        Changing the embedding model or dimension (migration 0002) nulls every stored
        vector: reviews and theme centroids alike. Reviews heal on their own (an analysed
        review with no embedding stays in the next run's working set), but a theme with no
        centroid is invisible to `nearest_themes`, so no review would ever link to it again
        and the same issue would come back as a duplicate theme. Its members are already
        linked (cluster skips them), so the centroid is rebuilt here, from the members that
        now have vectors, whenever one of them is re-embedded."""
        theme_ids = {t for ids in self.store.review_theme_ids(review_ids).values() for t in ids}
        restored = 0
        for theme_id in sorted(theme_ids):
            theme = self.store.get_theme(theme_id)
            if theme is None or theme.embedding is not None:
                continue
            members = self.store.get_reviews(self.store.theme_review_ids(theme_id))
            vectors = [r.embedding for r in members if r.embedding is not None]
            if vectors:
                self.store.update_theme(theme_id, embedding=centroid(vectors))
                restored += 1
        return restored

    # ------------------------------------------------------------------ cluster

    def cluster(self, working: list[str]) -> None:
        if self.cp.get("cluster") is not None:
            return
        config = self.routing.cluster
        reviews = [r for r in self._clusterable(working) if r.embedding is not None]
        already = self.store.review_theme_ids([r.id for r in reviews])
        reviews = [r for r in reviews if r.id not in already]
        if not reviews:
            self.cp.put("cluster", {"touched": [], "new": [], "linked": {}})
            return
        self.status("clustering")
        self.check_time("cluster")
        signals = self.store.get_signals([r.id for r in reviews])
        kinds = {r.id: theme_kind_for(signals[r.id].category) for r in reviews}
        self.step(
            "cluster",
            "tool_call",
            "cluster_reviews",
            args={
                "review_ids": [r.id for r in reviews],
                "link_threshold": config.link_threshold,
                "merge_threshold": config.merge_threshold,
                "within_kind": config.within_kind,
            },
        )
        # 1. memory first: link to existing themes.
        links: dict[str, list[tuple[Review, float]]] = defaultdict(list)
        leftover: list[Review] = []
        for review in reviews:
            assert review.embedding is not None
            nearest = self.store.nearest_themes(
                self.app.id,
                review.embedding,
                limit=1,
                kind=kinds[review.id] if config.within_kind else None,
            )
            if nearest and nearest[0][1] >= config.link_threshold:
                links[nearest[0][0].id].append((review, nearest[0][1]))
            else:
                leftover.append(review)
        linked_counts: dict[str, int] = {}
        for theme_id, members in links.items():
            theme = self.store.get_theme(theme_id)
            if theme is None:
                continue
            added = self.store.link_reviews(
                theme_id, [(r.id, round(s, 4)) for r, s in members], self.run.id
            )
            if theme.embedding is not None:
                new_centroid = updated_centroid(
                    theme.embedding, theme.review_count, [r.embedding for r, _ in members]
                )
            else:
                new_centroid = centroid([r.embedding for r, _ in members])
            self.store.update_theme(
                theme_id,
                review_count=theme.review_count + added,
                embedding=new_centroid,
                last_run_id=self.run.id,
            )
            linked_counts[theme_id] = added

        # 2. cluster the rest, within each kind if configured.
        groups: dict[str, list[Review]] = defaultdict(list)
        for review in leftover:
            groups[kinds[review.id] if config.within_kind else "all"].append(review)
        clusters: list[list[Review]] = []
        for members in groups.values():
            for indices in agglomerate([r.embedding for r in members], config.merge_threshold):
                if len(indices) >= config.min_cluster_size:
                    clusters.append([members[i] for i in indices])
        clusters.sort(key=len, reverse=True)

        # 3. name the new clusters (one model call), with deterministic fallbacks.
        names: dict[str, Any] = {}
        by_short: dict[str, Review] = {}
        usage = None
        to_name = clusters[:MAX_CLUSTERS_NAMED]
        if to_name and not self.budget_hit:
            message, _, by_short = build_naming_message(to_name)
            prompt = load_prompt("theme", self.routing.prompts.get("theme", "v1"))
            before = len(self.ledger.records)
            try:
                batch = self.theme_llm.complete_structured(
                    message, ThemeNameBatch, system=prompt.text, label="theme"
                )
                names = {item.ref.strip(): item for item in batch.themes}
            except LLMBudgetError as exc:
                self.budget_hit = True
                self.step("cluster", "note", "budget", result={"message": str(exc)})
            except Exception as exc:  # naming is cosmetic; never fail a run over it
                if "quota" in str(exc).lower() or "429" in str(exc):
                    raise
                self.step("cluster", "note", "naming_failed", result={"error": str(exc)[:300]})
            finally:
                self.save_ledger()
            usage = usage_since(self.ledger, before)

        new_ids: list[str] = []
        for index, members in enumerate(clusters, start=1):
            member_signals = [signals[r.id] for r in members]
            kind = Counter(kinds[r.id] for r in members).most_common(1)[0][0]
            area = Counter(s.feature_area for s in member_signals if s.feature_area).most_common(1)
            name = names.get(f"c{index}")
            title, summary = fallback_name(members, kind, area[0][0] if area else None)
            quotes = []
            if name is not None:
                title = name.name.strip()[:120] or title
                summary = name.summary.strip()[:300] or summary
                quotes = verified_quotes(name, by_short)
            if not quotes:
                quotes = [
                    ThemeQuote(review_id=s.review_id, text=s.quotes[0])
                    for s in member_signals
                    if s.quotes
                ][:3]
            theme = Theme(
                id=str(uuid4()),
                app_id=self.app.id,
                title=title,
                summary=summary,
                kind=kind,  # type: ignore[arg-type]
                review_count=len(members),
                quotes=quotes,
                embedding=centroid([r.embedding for r in members]),
                first_run_id=self.run.id,
                last_run_id=self.run.id,
            )
            self.store.create_theme(theme)
            self.store.link_reviews(theme.id, [(r.id, None) for r in members], self.run.id)
            new_ids.append(theme.id)
        result = {
            "linked": linked_counts,
            "new_themes": new_ids,
            "unthemed": len(leftover) - sum(len(c) for c in clusters),
        }
        self.step("cluster", "tool_result", "cluster_reviews", result=result, usage=usage)
        self.cp.put(
            "cluster",
            {"touched": list(linked_counts) + new_ids, "new": new_ids, "linked": linked_counts},
        )

    # ------------------------------------------------------------------ propose

    def build_context(self, working: list[str]) -> tuple[ToolContext, str]:
        cluster = self.cp.get("cluster") or {"touched": []}
        themes = [t for t in (self.store.get_theme(tid) for tid in cluster["touched"]) if t]
        themes.sort(key=lambda t: -t.review_count)
        themes = themes[:MAX_THEMES_IN_CONTEXT]
        members = {t.id: self.store.theme_review_ids(t.id) for t in themes}
        all_ids = list(dict.fromkeys([*working, *(r for m in members.values() for r in m)]))
        signals = self.store.get_signals(all_ids)
        reviews = {r.id: r for r in self.store.get_reviews(all_ids)}

        aliases: dict[str, Review] = {}
        review_alias: dict[str, str] = {}

        def alias_for(review_id: str) -> str:
            if review_id not in review_alias:
                review_alias[review_id] = f"R{len(review_alias) + 1}"
                aliases[review_alias[review_id]] = reviews[review_id]
            return review_alias[review_id]

        def clean(review_id: str) -> bool:
            sig = signals.get(review_id)
            return review_id in reviews and sig is not None and FLAG not in sig.flags

        theme_aliases: dict[str, Theme] = {}
        theme_blocks = []
        for index, theme in enumerate(themes, start=1):
            alias = f"T{index}"
            theme_aliases[alias] = theme
            existing = self.store.find_proposal("issue", theme_id=theme.id)
            shown = [rid for rid in members[theme.id] if clean(rid)][:MAX_MEMBERS_PER_THEME]
            severities = [signals[rid].severity for rid in members[theme.id] if rid in signals]
            inner = "\n".join(
                wrap_untrusted(
                    "review",
                    reviews[rid].text[:300],
                    id=alias_for(rid),
                    rating=str(reviews[rid].rating or "?"),
                )
                for rid in shown
            )
            header = (
                f'<theme id="{alias}" kind="{theme.kind}" reviews="{theme.review_count}" '
                f'max_severity="{max(severities, default=0)}" '
                f'issue_proposal="{existing.status if existing else "none"}">'
            )
            body = wrap_untrusted("summary", f"{theme.title}\n{theme.summary}")
            theme_blocks.append(f"{header}\n{body}\n{inner}\n</theme>")

        candidates = [
            rid
            for rid in working
            if clean(rid)
            and signals[rid].category != "praise"
            and not signals[rid].flags
            and self.store.find_proposal("reply", review_id=rid) is None
        ]
        candidates.sort(key=lambda rid: (-signals[rid].severity, reviews[rid].rating or 5))
        review_blocks = []
        for rid in candidates[:MAX_REPLY_CANDIDATES]:
            sig = signals[rid]
            review_blocks.append(
                wrap_untrusted(
                    "review",
                    reviews[rid].text[:500],
                    id=alias_for(rid),
                    rating=str(reviews[rid].rating or "?"),
                    category=sig.category,
                    severity=str(sig.severity),
                    version=reviews[rid].app_version or "?",
                )
            )
        quarantined = sum(1 for rid in working if rid in signals and FLAG in signals[rid].flags)
        ctx = ToolContext(
            store=self.store,
            app=self.app,
            run_id=self.run.id,
            reviews=aliases,
            signals=signals,
            themes=theme_aliases,
            theme_members=members,
            embedder=self.embedder,
            max_replies=self.max_replies,
            max_issues=self.max_issues,
        )
        message = "\n\n".join(
            [
                f"App: {self.app.name} ({self.app.store}, {self.app.country}). This run "
                f"triaged {len(working)} new reviews; {quarantined} were quarantined as "
                "suspected prompt injection and are not shown.",
                "## Themes touched by this run\n"
                + ("\n\n".join(theme_blocks) or "(no themes this run)"),
                "## Reviews that may deserve a reply (most severe first)\n"
                + ("\n\n".join(review_blocks) or "(none)"),
                "Decide what to propose, use the tools, then call summarise_run.",
            ]
        )
        return ctx, message

    def _on_step(self, ctx: ToolContext) -> Callable[[int, Any, list[Any]], None]:
        marker = {"before": len(self.ledger.records)}

        def record(iteration: int, message: Any, outcomes: list[Any]) -> None:
            calls = getattr(message, "tool_calls", None) or []
            self.step(
                "propose",
                "model",
                getattr(self.agent_llm, "model", "agent"),
                args={"iteration": iteration},
                result={
                    "content": (getattr(message, "content", None) or "")[:2000],
                    "tool_calls": [call.function.name for call in calls],
                },
                usage=usage_since(self.ledger, marker["before"]),
            )
            marker["before"] = len(self.ledger.records)
            for call, outcome in zip(calls, outcomes, strict=False):
                try:
                    parsed = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    parsed = {"_raw": (call.function.arguments or "")[:2000]}
                self.step("propose", "tool_call", outcome.name, args=parsed)
                self.step(
                    "propose",
                    "tool_result",
                    outcome.name,
                    result={
                        "ok": outcome.ok,
                        "content": outcome.content[:2000],
                        "detail": outcome.detail,
                    },
                )
            self.save_ledger()

        return record

    def propose(self, working: list[str]) -> dict[str, Any]:
        saved = self.cp.get("propose")
        if saved is not None:
            return saved
        if self.budget_hit:
            outcome = {"stop_reason": "budget", "iterations": 0, "replies": 0, "issues": 0}
            self.cp.put("propose", outcome)
            return outcome
        config = self.routing.agent
        if self.remaining() < config.min_window_s + SUSPEND_MARGIN_S:
            self.step(
                "propose", "note", "suspended", result={"remaining_s": round(self.remaining())}
            )
            raise RunSuspended("not enough time left to start the propose loop")
        self.status("proposing")
        ctx, message = self.build_context(working)
        if not ctx.themes and not ctx.reviews:
            outcome = {
                "stop_reason": "nothing_to_propose",
                "iterations": 0,
                "replies": 0,
                "issues": 0,
            }
            self.step("propose", "note", "skipped", result=outcome)
            self.cp.put("propose", outcome)
            return outcome
        prompt = load_prompt("agent", self.routing.prompts.get("agent", "v1"))
        system = (
            prompt.text.replace("{policy}", self.app.policy.strip() or "(none)")
            .replace("{max_replies}", str(self.max_replies))
            .replace("{max_issues}", str(self.max_issues))
        )
        deadline_s = min(config.deadline_s, self.remaining() - SUSPEND_MARGIN_S)
        self.step(
            "propose",
            "note",
            "agent_loop",
            args={
                "max_iterations": config.max_iterations,
                "deadline_s": round(deadline_s),
                "max_usd": self.ledger.max_usd,
                "themes": len(ctx.themes),
                "reply_candidates": sum(1 for _ in ctx.reviews),
                "prompt_version": prompt.id,
            },
        )
        result = self.agent_llm.call_tools(
            message,
            build_tools(ctx),
            system=system,
            label="agent",
            max_iterations=config.max_iterations,
            deadline_s=deadline_s,
            on_step=self._on_step(ctx),
        )
        self.save_ledger()
        outcome = {
            "stop_reason": result.stop_reason,
            "iterations": result.iterations,
            "replies": ctx.replies,
            "issues": ctx.issues,
            "guardrail_checks": ctx.guardrail_checks,
            "summary": ctx.summary,
            "created": ctx.created,
            "final": (result.text or "")[:500],
        }
        self.step(
            "propose",
            "note",
            "agent_stopped",
            result={k: v for k, v in outcome.items() if k != "created"},
        )
        self.cp.put("propose", outcome)
        return outcome

    # ------------------------------------------------------------------ finish

    def finish(self, working: list[str], outcome: dict[str, Any]) -> WorkflowResult:
        signals = self.store.get_signals(working)
        fetch = self.cp.get("fetch") or {}
        cluster = self.cp.get("cluster") or {}
        stats = {
            "fetched": fetch.get("fetched", 0),
            "new_reviews": fetch.get("new", 0),
            "working_set": len(working),
            "analysed": len(signals),
            "quarantined": sum(1 for s in signals.values() if FLAG in s.flags),
            "categories": dict(Counter(s.category for s in signals.values())),
            "themes_new": len(cluster.get("new", [])),
            "themes_linked": len(cluster.get("linked", {})),
            "replies_proposed": outcome.get("replies", 0),
            "issues_proposed": outcome.get("issues", 0),
            "stop_reason": outcome.get("stop_reason"),
            "iterations": outcome.get("iterations", 0),
            "budget_hit": self.budget_hit or outcome.get("stop_reason") == "budget",
        }
        summary = outcome.get("summary") or (
            f"Triaged {stats['analysed']} reviews: {stats['themes_new']} new themes, "
            f"{stats['themes_linked']} existing themes updated, {stats['replies_proposed']} "
            f"replies and {stats['issues_proposed']} issues proposed."
        )
        self.step("finish", "note", "summary", result={"summary": summary, "stats": stats})
        return WorkflowResult(
            summary=summary, stats=stats, created_proposals=outcome.get("created", [])
        )
