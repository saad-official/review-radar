"""The persistence boundary: one `Store` protocol, two implementations.

    PostgresStore   production (Neon + pgvector), db/postgres.py
    MemoryStore     tests, evals, and a keyless local demo

The protocol is synchronous for the same reason as in Changelog Forge: llm-kit is
synchronous, so a run executes in a worker thread that can call a sync store directly.

Memory is just tables (decision 0002): themes, theme_reviews and proposals *are* the
agent's long-term memory, read through `search_memory` and enforced by constraints (one
reply proposal per review, one issue proposal per theme, ever).
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from ..embeddings import dot
from ..ingest.base import ReviewIn
from ..models import (
    TERMINAL_RUN_STATUSES,
    AgentEvent,
    App,
    MemoryHit,
    Proposal,
    Review,
    Run,
    RunStep,
    Signals,
    Theme,
    utcnow,
)

RRF_K = 60
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    ]
)


def keywords(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal rank fusion: robust to the two scores being on different scales."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] += 1.0 / (k + rank)
    return dict(scores)


class Store(Protocol):
    def ping(self) -> bool: ...

    # -- apps -------------------------------------------------------------------------
    def create_app(self, app: App) -> App: ...
    def get_app(self, app_id: str) -> App | None: ...
    def find_app(self, store: str, store_id: str, country: str) -> App | None: ...
    def list_apps(self) -> list[App]: ...
    def update_app(self, app_id: str, **fields: Any) -> None: ...
    def delete_app(self, app_id: str) -> bool: ...
    def app_counts(self, app_id: str) -> dict[str, Any]: ...

    # -- reviews and signals -------------------------------------------------------------
    def upsert_reviews(self, app_id: str, reviews: list[ReviewIn]) -> list[str]: ...
    def known_review_ids(self, app_id: str) -> set[str]: ...
    def get_reviews(self, review_ids: list[str]) -> list[Review]: ...
    def list_reviews(
        self,
        app_id: str,
        *,
        theme_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Review]: ...
    def unprocessed_review_ids(self, app_id: str, limit: int) -> list[str]: ...
    def set_embeddings(self, vectors: dict[str, list[float]]) -> None: ...
    def put_signals(self, signals: list[Signals]) -> None: ...
    def get_signals(self, review_ids: list[str]) -> dict[str, Signals]: ...

    # -- themes ---------------------------------------------------------------------------
    def create_theme(self, theme: Theme) -> Theme: ...
    def get_theme(self, theme_id: str) -> Theme | None: ...
    def update_theme(self, theme_id: str, **fields: Any) -> None: ...
    def list_themes(self, app_id: str, *, status: str | None = None) -> list[Theme]: ...
    def nearest_themes(
        self, app_id: str, vector: list[float], *, limit: int = 5, kind: str | None = None
    ) -> list[tuple[Theme, float]]: ...
    def link_reviews(
        self, theme_id: str, links: list[tuple[str, float | None]], run_id: str | None
    ) -> int: ...
    def theme_review_ids(self, theme_id: str) -> list[str]: ...
    def review_theme_ids(self, review_ids: list[str]) -> dict[str, list[str]]: ...

    # -- proposals ------------------------------------------------------------------------
    def create_proposal(self, proposal: Proposal) -> Proposal | None: ...
    def get_proposal(self, proposal_id: str) -> Proposal | None: ...
    def list_proposals(
        self,
        app_id: str,
        *,
        status: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
    ) -> list[Proposal]: ...
    def find_proposal(
        self, kind: str, *, review_id: str | None = None, theme_id: str | None = None
    ) -> Proposal | None: ...
    def transition_proposal(
        self, proposal_id: str, from_statuses: tuple[str, ...], **fields: Any
    ) -> Proposal | None: ...

    # -- runs -----------------------------------------------------------------------------
    def create_run(self, run: Run) -> Run: ...
    def get_run(self, run_id: str) -> Run | None: ...
    def update_run(self, run_id: str, **fields: Any) -> None: ...
    def claim_run(self, run_id: str, lease_s: float, max_attempts: int) -> Run | None: ...
    def list_runs(self, app_id: str, *, limit: int = 20) -> list[Run]: ...
    def count_runs_since(self, client_key: str, since: datetime) -> int: ...
    def put_checkpoint(self, run_id: str, key: str, data: Any) -> None: ...
    def get_checkpoints(self, run_id: str) -> dict[str, Any]: ...
    def append_step(
        self,
        run_id: str,
        stage: str,
        kind: str,
        name: str,
        args: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> RunStep: ...
    def list_steps(self, run_id: str, after_seq: int = 0) -> list[RunStep]: ...

    # -- audit and memory -------------------------------------------------------------------
    def append_event(self, event: AgentEvent) -> AgentEvent: ...
    def list_events(self, app_id: str, *, limit: int = 100) -> list[AgentEvent]: ...
    def search_memory(
        self, app_id: str, query: str, vector: list[float] | None, *, limit: int = 8
    ) -> list[MemoryHit]: ...


def is_claimable(run: Run, now: datetime, max_attempts: int) -> bool:
    """Unfinished, unleased, and not out of attempts. The lease makes `/process` safe to call
    twice; its expiry makes a killed request recoverable from the last checkpoint."""
    if run.status in TERMINAL_RUN_STATUSES or run.attempts >= max_attempts:
        return False
    return run.lease_until is None or run.lease_until <= now


def proposal_text(proposal: Proposal) -> str:
    draft = proposal.draft
    return " ".join(
        str(part) for part in (draft.get("title"), draft.get("summary"), draft.get("text")) if part
    )


class MemoryStore:
    """A thread-safe in-process Store with the same semantics as Postgres, no durability."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.apps: dict[str, App] = {}
        self.reviews: dict[str, Review] = {}
        self._review_keys: dict[tuple[str, str], str] = {}
        self.signals: dict[str, Signals] = {}
        self.themes: dict[str, Theme] = {}
        self.theme_reviews: dict[str, dict[str, float | None]] = defaultdict(dict)
        self.proposals: dict[str, Proposal] = {}
        self.runs: dict[str, Run] = {}
        self.checkpoints: dict[str, dict[str, Any]] = defaultdict(dict)
        self.steps: dict[str, list[RunStep]] = defaultdict(list)
        self.events: list[AgentEvent] = []

    def ping(self) -> bool:
        return True

    # -- apps -------------------------------------------------------------------------
    def create_app(self, app: App) -> App:
        with self._lock:
            if self.find_app(app.store, app.store_id, app.country):
                raise ValueError("app already exists")
            self.apps[app.id] = app.model_copy(deep=True)
            return app

    def get_app(self, app_id: str) -> App | None:
        with self._lock:
            app = self.apps.get(app_id)
            return app.model_copy(deep=True) if app else None

    def find_app(self, store: str, store_id: str, country: str) -> App | None:
        with self._lock:
            for app in self.apps.values():
                if (app.store, app.store_id, app.country) == (store, store_id, country):
                    return app.model_copy(deep=True)
            return None

    def list_apps(self) -> list[App]:
        with self._lock:
            return sorted(
                (a.model_copy(deep=True) for a in self.apps.values()), key=lambda a: a.created_at
            )

    def update_app(self, app_id: str, **fields: Any) -> None:
        with self._lock:
            self.apps[app_id] = self.apps[app_id].model_copy(update=fields)

    def delete_app(self, app_id: str) -> bool:
        with self._lock:
            if self.apps.pop(app_id, None) is None:
                return False
            review_ids = {r.id for r in self.reviews.values() if r.app_id == app_id}
            theme_ids = {t.id for t in self.themes.values() if t.app_id == app_id}
            run_ids = {r.id for r in self.runs.values() if r.app_id == app_id}
            for rid in review_ids:
                self.reviews.pop(rid, None)
                self.signals.pop(rid, None)
            self._review_keys = {k: v for k, v in self._review_keys.items() if k[0] != app_id}
            for tid in theme_ids:
                self.themes.pop(tid, None)
                self.theme_reviews.pop(tid, None)
            for run_id in run_ids:
                self.runs.pop(run_id, None)
                self.steps.pop(run_id, None)
                self.checkpoints.pop(run_id, None)
            self.proposals = {k: p for k, p in self.proposals.items() if p.app_id != app_id}
            self.events = [e for e in self.events if e.app_id != app_id]
            return True

    def app_counts(self, app_id: str) -> dict[str, Any]:
        with self._lock:
            review_ids = [r.id for r in self.reviews.values() if r.app_id == app_id]
            proposals = [p for p in self.proposals.values() if p.app_id == app_id]
            by_status: dict[str, int] = defaultdict(int)
            for proposal in proposals:
                by_status[proposal.status] += 1
            return {
                "reviews": len(review_ids),
                "analysed": sum(1 for rid in review_ids if rid in self.signals),
                "themes": sum(1 for t in self.themes.values() if t.app_id == app_id),
                "proposals": dict(by_status),
            }

    # -- reviews -----------------------------------------------------------------------
    def upsert_reviews(self, app_id: str, reviews: list[ReviewIn]) -> list[str]:
        with self._lock:
            new_ids = []
            for item in reviews:
                key = (app_id, item.store_review_id)
                if key in self._review_keys:
                    continue
                review = Review(id=str(uuid4()), app_id=app_id, **item.model_dump())
                self.reviews[review.id] = review
                self._review_keys[key] = review.id
                new_ids.append(review.id)
            return new_ids

    def known_review_ids(self, app_id: str) -> set[str]:
        with self._lock:
            return {sid for (aid, sid) in self._review_keys if aid == app_id}

    def get_reviews(self, review_ids: list[str]) -> list[Review]:
        with self._lock:
            return [self.reviews[r].model_copy(deep=True) for r in review_ids if r in self.reviews]

    def _sorted_reviews(self, app_id: str) -> list[Review]:
        reviews = [r for r in self.reviews.values() if r.app_id == app_id]
        epoch = datetime.min.replace(tzinfo=utcnow().tzinfo)
        return sorted(reviews, key=lambda r: (r.date or epoch, r.store_review_id), reverse=True)

    def list_reviews(
        self, app_id: str, *, theme_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Review]:
        with self._lock:
            reviews = self._sorted_reviews(app_id)
            if theme_id is not None:
                members = self.theme_reviews.get(theme_id, {})
                reviews = [r for r in reviews if r.id in members]
            return [r.model_copy(deep=True) for r in reviews[offset : offset + limit]]

    def unprocessed_review_ids(self, app_id: str, limit: int) -> list[str]:
        """Reviews a run still owes work: no signals yet, or analysed and clean but never
        embedded (an embed stage that failed on a provider error)."""
        with self._lock:

            def owed(review: Review) -> bool:
                signal = self.signals.get(review.id)
                if signal is None:
                    return True
                return review.embedding is None and "prompt_injection" not in signal.flags

            return [r.id for r in self._sorted_reviews(app_id) if owed(r)][:limit]

    def set_embeddings(self, vectors: dict[str, list[float]]) -> None:
        with self._lock:
            for review_id, vector in vectors.items():
                self.reviews[review_id] = self.reviews[review_id].model_copy(
                    update={"embedding": list(vector)}
                )

    def put_signals(self, signals: list[Signals]) -> None:
        with self._lock:
            for signal in signals:
                self.signals[signal.review_id] = signal.model_copy(deep=True)

    def get_signals(self, review_ids: list[str]) -> dict[str, Signals]:
        with self._lock:
            return {
                r: self.signals[r].model_copy(deep=True) for r in review_ids if r in self.signals
            }

    # -- themes -------------------------------------------------------------------------
    def create_theme(self, theme: Theme) -> Theme:
        with self._lock:
            self.themes[theme.id] = theme.model_copy(deep=True)
            return theme

    def get_theme(self, theme_id: str) -> Theme | None:
        with self._lock:
            theme = self.themes.get(theme_id)
            return theme.model_copy(deep=True) if theme else None

    def update_theme(self, theme_id: str, **fields: Any) -> None:
        with self._lock:
            fields.setdefault("updated_at", utcnow())
            self.themes[theme_id] = self.themes[theme_id].model_copy(update=fields)

    def list_themes(self, app_id: str, *, status: str | None = None) -> list[Theme]:
        with self._lock:
            themes = [
                t.model_copy(deep=True)
                for t in self.themes.values()
                if t.app_id == app_id and (status is None or t.status == status)
            ]
            return sorted(themes, key=lambda t: (-t.review_count, t.created_at))

    def nearest_themes(
        self, app_id: str, vector: list[float], *, limit: int = 5, kind: str | None = None
    ) -> list[tuple[Theme, float]]:
        with self._lock:
            scored = [
                (t.model_copy(deep=True), dot(t.embedding, vector))
                for t in self.themes.values()
                if t.app_id == app_id
                and t.embedding is not None
                and t.status != "ignored"
                and (kind is None or t.kind == kind)
            ]
            return sorted(scored, key=lambda pair: -pair[1])[:limit]

    def link_reviews(
        self, theme_id: str, links: list[tuple[str, float | None]], run_id: str | None
    ) -> int:
        with self._lock:
            members = self.theme_reviews[theme_id]
            added = 0
            for review_id, similarity in links:
                if review_id not in members:
                    members[review_id] = similarity
                    added += 1
            return added

    def theme_review_ids(self, theme_id: str) -> list[str]:
        with self._lock:
            return list(self.theme_reviews.get(theme_id, {}))

    def review_theme_ids(self, review_ids: list[str]) -> dict[str, list[str]]:
        with self._lock:
            wanted = set(review_ids)
            result: dict[str, list[str]] = defaultdict(list)
            for theme_id, members in self.theme_reviews.items():
                for review_id in members:
                    if review_id in wanted:
                        result[review_id].append(theme_id)
            return dict(result)

    # -- proposals ------------------------------------------------------------------------
    def create_proposal(self, proposal: Proposal) -> Proposal | None:
        with self._lock:
            if proposal.kind == "reply" and self.find_proposal(
                "reply", review_id=proposal.review_id
            ):
                return None
            if proposal.kind == "issue" and self.find_proposal("issue", theme_id=proposal.theme_id):
                return None
            self.proposals[proposal.id] = proposal.model_copy(deep=True)
            return proposal

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        with self._lock:
            proposal = self.proposals.get(proposal_id)
            return proposal.model_copy(deep=True) if proposal else None

    def list_proposals(
        self,
        app_id: str,
        *,
        status: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
    ) -> list[Proposal]:
        with self._lock:
            found = [
                p.model_copy(deep=True)
                for p in self.proposals.values()
                if p.app_id == app_id
                and (status is None or p.status == status)
                and (kind is None or p.kind == kind)
                and (run_id is None or p.run_id == run_id)
            ]
            return sorted(found, key=lambda p: p.created_at, reverse=True)

    def find_proposal(
        self, kind: str, *, review_id: str | None = None, theme_id: str | None = None
    ) -> Proposal | None:
        with self._lock:
            for proposal in self.proposals.values():
                if proposal.kind != kind:
                    continue
                if kind == "reply" and proposal.review_id == review_id:
                    return proposal.model_copy(deep=True)
                if kind == "issue" and proposal.theme_id == theme_id:
                    return proposal.model_copy(deep=True)
            return None

    def transition_proposal(
        self, proposal_id: str, from_statuses: tuple[str, ...], **fields: Any
    ) -> Proposal | None:
        with self._lock:
            proposal = self.proposals.get(proposal_id)
            if proposal is None or proposal.status not in from_statuses:
                return None
            updated = proposal.model_copy(update={**fields, "updated_at": utcnow()})
            self.proposals[proposal_id] = updated
            return updated.model_copy(deep=True)

    # -- runs -----------------------------------------------------------------------------
    def create_run(self, run: Run) -> Run:
        with self._lock:
            self.runs[run.id] = run.model_copy(deep=True)
            return run

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            run = self.runs.get(run_id)
            return run.model_copy(deep=True) if run else None

    def update_run(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            self.runs[run_id] = self.runs[run_id].model_copy(update=fields)

    def claim_run(self, run_id: str, lease_s: float, max_attempts: int) -> Run | None:
        with self._lock:
            run = self.runs.get(run_id)
            now = utcnow()
            if run is None or not is_claimable(run, now, max_attempts):
                return None
            claimed = run.model_copy(
                update={
                    "attempts": run.attempts + 1,
                    "lease_until": now + timedelta(seconds=lease_s),
                    "started_at": run.started_at or now,
                }
            )
            self.runs[run_id] = claimed
            return claimed.model_copy(deep=True)

    def list_runs(self, app_id: str, *, limit: int = 20) -> list[Run]:
        with self._lock:
            runs = [r.model_copy(deep=True) for r in self.runs.values() if r.app_id == app_id]
            return sorted(runs, key=lambda r: r.created_at, reverse=True)[:limit]

    def count_runs_since(self, client_key: str, since: datetime) -> int:
        with self._lock:
            return sum(
                1
                for r in self.runs.values()
                if r.client_key == client_key and r.created_at >= since
            )

    def put_checkpoint(self, run_id: str, key: str, data: Any) -> None:
        with self._lock:
            self.checkpoints[run_id][key] = data

    def get_checkpoints(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self.checkpoints.get(run_id, {}))

    def append_step(
        self,
        run_id: str,
        stage: str,
        kind: str,
        name: str,
        args: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> RunStep:
        with self._lock:
            steps = self.steps[run_id]
            step = RunStep(
                run_id=run_id,
                seq=len(steps) + 1,
                stage=stage,
                kind=kind,  # type: ignore[arg-type]
                name=name,
                args=args or {},
                result=result or {},
                usage=usage,
            )
            steps.append(step)
            return step.model_copy(deep=True)

    def list_steps(self, run_id: str, after_seq: int = 0) -> list[RunStep]:
        with self._lock:
            return [
                s.model_copy(deep=True) for s in self.steps.get(run_id, []) if s.seq > after_seq
            ]

    # -- audit and memory -------------------------------------------------------------------
    def append_event(self, event: AgentEvent) -> AgentEvent:
        with self._lock:
            self.events.append(event.model_copy(deep=True))
            return event

    def list_events(self, app_id: str, *, limit: int = 100) -> list[AgentEvent]:
        with self._lock:
            found = [e for e in self.events if e.app_id == app_id]
            return list(reversed(found))[:limit]

    def search_memory(
        self, app_id: str, query: str, vector: list[float] | None, *, limit: int = 8
    ) -> list[MemoryHit]:
        with self._lock:
            words = set(keywords(query))
            themes = [t for t in self.themes.values() if t.app_id == app_id]
            proposals = [p for p in self.proposals.values() if p.app_id == app_id]
            hits: dict[str, MemoryHit] = {}

            def overlap(text: str) -> int:
                return len(words & set(keywords(text)))

            keyword_rank: list[tuple[int, str]] = []
            vector_rank: list[tuple[float, str]] = []
            for theme in themes:
                key = f"theme:{theme.id}"
                hits[key] = MemoryHit(
                    kind="theme",
                    id=theme.id,
                    title=theme.title,
                    snippet=theme.summary[:300],
                    status=theme.status,
                    score=0.0,
                    theme_id=theme.id,
                )
                score = overlap(f"{theme.title} {theme.summary}")
                if score:
                    keyword_rank.append((score, key))
                if vector is not None and theme.embedding is not None:
                    vector_rank.append((dot(theme.embedding, vector), key))
            for proposal in proposals:
                key = f"proposal:{proposal.id}"
                text = proposal_text(proposal)
                hits[key] = MemoryHit(
                    kind="proposal",
                    id=proposal.id,
                    title=f"{proposal.kind}: {(proposal.draft.get('title') or text)[:120]}",
                    snippet=text[:300],
                    status=proposal.status,
                    score=0.0,
                    theme_id=proposal.theme_id,
                    decision_reason=proposal.decision_reason,
                )
                score = overlap(f"{text} {proposal.decision_reason or ''}")
                if score:
                    keyword_rank.append((score, key))
                theme = self.themes.get(proposal.theme_id or "")
                if vector is not None and theme is not None and theme.embedding is not None:
                    vector_rank.append((dot(theme.embedding, vector) - 0.001, key))
            rankings = [
                [key for _, key in sorted(keyword_rank, key=lambda p: -p[0])][:20],
                [key for score, key in sorted(vector_rank, key=lambda p: -p[0]) if score > 0.3][
                    :20
                ],
            ]
            fused = rrf(rankings)
            ordered = sorted(fused.items(), key=lambda p: -p[1])[:limit]
            return [
                hits[key].model_copy(update={"score": round(score, 5)}) for key, score in ordered
            ]
