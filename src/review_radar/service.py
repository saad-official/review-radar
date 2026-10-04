"""Use cases on top of the Store: apps, runs (create -> process, resumable), the approval gate.

Run state machine (docs/architecture.md):

    queued --process--> fetching -> extracting -> embedding -> clustering -> proposing -> done
       |                    \\__________________ any stage _______________________/
       |                             | time budget: lease released, status kept, the next
       |                             | /process resumes from the stage checkpoints
       +-------------------------------> failed (provider quota, unexpected error, attempts)

Proposal state machine (decision 0003):

    proposed --approve--> approved --(issue: executor)--> executed
        |                     \\------------------------> failed --approve again--> ...
        +--reject(reason)--> rejected         (the reason is memory: search_memory shows it)

Approval is idempotent: approving an executed proposal returns it unchanged, and the
`proposed -> approved` transition is one conditional UPDATE, so two concurrent approvals
create at most one GitHub issue.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .agent.guardrails import Policy, check_issue, check_reply
from .agent.templates import IssueDraft, render_issue_body
from .agent.workflow import Checkpoints, RunSuspended, RunWorkflow, ledger_from_json
from .crypto import SecretboxError, decrypt_secret, encrypt_secret
from .db import Store
from .engine import Engine
from .executors.github import GitHubIssueError
from .ingest.base import IngestError, ReviewIn
from .llm import LLMRouteError
from .models import AgentEvent, App, Proposal, Run, Usage, utcnow

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
DEFAULT_TIME_BUDGET_S = 240.0


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def audit(store: Store, actor: str, type_: str, **fields: Any) -> None:
    try:
        store.append_event(AgentEvent(id=str(uuid4()), actor=actor, type=type_, **fields))  # type: ignore[arg-type]
    except Exception:
        log.exception("could not append audit event %s", type_)


# ---------------------------------------------------------------------- apps


def create_app(
    engine: Engine,
    *,
    store_kind: str,
    store_id: str,
    name: str,
    country: str = "us",
    github_repo: str | None = None,
    policy: str = "",
    public: bool = False,
    github_token: str | None = None,
) -> App:
    store = engine.store
    if store.find_app(store_kind, store_id, country) is not None:
        raise ServiceError(409, "app_exists", "this app is already registered")
    app = App(
        id=str(uuid4()),
        store=store_kind,  # type: ignore[arg-type]
        store_id=store_id,
        name=name,
        country=country,
        github_repo=github_repo,
        policy=policy,
        public=public,
    )
    if github_token:
        app.github_token_ciphertext = seal_token(engine, app.id, github_token)
    store.create_app(app)
    audit(
        store,
        "operator",
        "app.created",
        app_id=app.id,
        entity_type="app",
        entity_id=app.id,
        input={"store": store_kind, "store_id": store_id, "country": country, "public": public},
    )
    return app


def seal_token(engine: Engine, app_id: str, token: str) -> str:
    try:
        return encrypt_secret(
            engine.settings.secret(engine.settings.app_encryption_key), app_id, token.strip()
        )
    except SecretboxError as exc:
        raise ServiceError(422, "encryption_unavailable", str(exc)) from exc


def import_reviews(engine: Engine, app: App, reviews: list[ReviewIn]) -> list[str]:
    new_ids = engine.store.upsert_reviews(app.id, reviews)
    audit(
        engine.store,
        "operator",
        "reviews.imported",
        app_id=app.id,
        entity_type="app",
        entity_id=app.id,
        output={"rows": len(reviews), "new": len(new_ids)},
    )
    return new_ids


def seed_from_feed(engine: Engine, app: App, *, max_pages: int = 4) -> dict[str, Any]:
    """Ingest without any model call (used by the demo seed and the live smoke)."""
    source = engine.source_builder(app)
    if source is None:
        raise IngestError("not_supported", "no source for this app")
    known = engine.store.known_review_ids(app.id)
    fetched = source.fetch(app.store_id, app.country, known=known, max_pages=max_pages)
    new_ids = engine.store.upsert_reviews(app.id, fetched.reviews)
    return {"fetched": len(fetched.reviews), "new": len(new_ids), "pages": fetched.pages}


# ---------------------------------------------------------------------- runs


@dataclass
class ProcessOutcome:
    run_id: str
    status: str
    resumable: bool = False
    message: str = ""


class RunService:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.store = engine.store

    def create_run(
        self,
        app_id: str,
        *,
        trigger: str = "manual",
        client_key: str | None = None,
        max_reviews: int | None = None,
    ) -> Run:
        settings = self.engine.settings
        routing = self.engine.routing
        run = Run(
            id=str(uuid4()),
            app_id=app_id,
            trigger=trigger,  # type: ignore[arg-type]
            client_key=client_key,
            budget={
                "max_usd": self.engine.budget_usd,
                "max_iterations": routing.agent.max_iterations,
                "deadline_s": routing.agent.deadline_s,
                "max_reviews": max_reviews or settings.max_reviews_per_run,
                "process_time_budget_s": settings.process_time_budget_s,
                "routing": routing.summary(),
                "prompts": dict(routing.prompts),
            },
        )
        self.store.create_run(run)
        self.store.append_step(run.id, "queued", "note", "status", result={"status": "queued"})
        audit(
            self.store,
            "cron" if trigger == "cron" else "operator",
            "run.created",
            app_id=app_id,
            entity_type="run",
            entity_id=run.id,
            input={"trigger": trigger},
        )
        return run

    def _fail(self, run_id: str, message: str, usage: dict[str, Any] | None = None) -> None:
        fields: dict[str, Any] = {
            "status": "failed",
            "error": message[:1000],
            "lease_until": None,
            "finished_at": utcnow(),
        }
        if usage is not None:
            fields["usage"] = usage
        self.store.update_run(run_id, **fields)
        self.store.append_step(
            run_id, "finish", "note", "status", result={"status": "failed", "error": message[:500]}
        )

    def process(self, run_id: str, time_budget_s: float | None = None) -> ProcessOutcome:
        """Run (or resume) a queued run. Idempotent and safe to race (lease)."""
        budget_s = time_budget_s or DEFAULT_TIME_BUDGET_S
        claimed = self.store.claim_run(run_id, lease_s=budget_s + 30, max_attempts=MAX_ATTEMPTS)
        if claimed is None:
            run = self.store.get_run(run_id)
            if run is None:
                return ProcessOutcome(run_id, "missing", message="run not found")
            if run.status in ("done", "failed"):
                return ProcessOutcome(run_id, run.status, message="already finished")
            if run.attempts >= MAX_ATTEMPTS:
                message = f"gave up after {run.attempts} attempts"
                self._fail(run_id, message)
                return ProcessOutcome(run_id, "failed", message=message)
            return ProcessOutcome(run_id, "in_progress", message="already being processed")

        app = self.store.get_app(claimed.app_id)
        if app is None:
            self._fail(run_id, "app not found")
            return ProcessOutcome(run_id, "failed", message="app not found")
        budget_usd = float(claimed.budget.get("max_usd") or self.engine.budget_usd)
        checkpoints = Checkpoints(self.store, run_id)
        ledger = ledger_from_json(checkpoints.get("ledger"), budget_usd)
        extract_llm, theme_llm, agent_llm = self.engine.llm_builder(ledger)

        def on_status(status: str) -> None:
            self.store.update_run(run_id, status=status)

        workflow = RunWorkflow(
            store=self.store,
            routing=self.engine.routing,
            app=app,
            run=claimed,
            ledger=ledger,
            extract_llm=extract_llm,
            theme_llm=theme_llm,
            agent_llm=agent_llm,
            embedder=self.engine.embedder_builder(ledger),
            source=self.engine.source_builder(app),
            deadline=time.monotonic() + budget_s,
            max_reviews=int(claimed.budget.get("max_reviews") or 50),
            feed_max_pages=self.engine.settings.feed_max_pages,
            on_status=on_status,
            checkpoints=checkpoints,
        )

        def usage() -> dict[str, Any]:
            return Usage.from_ledger(ledger, budget_usd).model_dump(mode="json")

        try:
            result = workflow.execute()
        except RunSuspended as exc:
            self.store.update_run(
                run_id, attempts=max(0, claimed.attempts - 1), lease_until=None, usage=usage()
            )
            return ProcessOutcome(run_id, "queued", resumable=True, message=str(exc))
        except LLMRouteError as exc:
            code = "model_quota_exhausted" if exc.quota_exhausted else "model_unavailable"
            message = f"{code}: {exc}"
            self._fail(run_id, message, usage())
            return ProcessOutcome(run_id, "failed", message=message)
        except Exception as exc:
            log.exception("run %s failed", run_id)
            message = f"{type(exc).__name__}: {exc}"
            self._fail(run_id, message, usage())
            return ProcessOutcome(run_id, "failed", message=message)

        run_usage = usage()
        self.store.update_run(
            run_id,
            status="done",
            summary=result.summary,
            stats=result.stats,
            usage=run_usage,
            lease_until=None,
            finished_at=utcnow(),
        )
        self.store.append_step(
            run_id,
            "finish",
            "note",
            "status",
            result={"status": "done", "usage": {k: run_usage[k] for k in ("calls", "usd")}},
        )
        for proposal_id in result.created_proposals:
            proposal = self.store.get_proposal(proposal_id)
            if proposal is not None:
                audit(
                    self.store,
                    "agent",
                    "proposal.created",
                    app_id=app.id,
                    entity_type="proposal",
                    entity_id=proposal.id,
                    output={"kind": proposal.kind, "run_id": run_id},
                )
        audit(
            self.store,
            "agent",
            "run.completed",
            app_id=app.id,
            entity_type="run",
            entity_id=run_id,
            output=result.stats,
            tokens_in=run_usage["prompt_tokens"],
            tokens_out=run_usage["completion_tokens"],
        )
        try:
            from .observability import export_run

            export_run(self.engine.settings, run_id, ledger, {"app": app.name})
        except Exception:
            log.exception("trace export failed for run %s", run_id)
        return ProcessOutcome(run_id, "done")


# ---------------------------------------------------------------------- approval gate


class ProposalService:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.store = engine.store

    def _get(self, proposal_id: str) -> Proposal:
        try:
            proposal = self.store.get_proposal(proposal_id)
        except Exception as exc:
            raise ServiceError(404, "proposal_not_found", "proposal not found") from exc
        if proposal is None:
            raise ServiceError(404, "proposal_not_found", "proposal not found")
        return proposal

    def _edited_draft(self, proposal: Proposal, app: App, edits: dict[str, Any]) -> dict[str, Any]:
        """Apply operator edits and re-run the same guardrails: a human edit is also checked."""
        draft = dict(proposal.draft)
        if proposal.kind == "reply":
            text = str(edits.get("text") or edits.get("body") or draft.get("text", "")).strip()
            review = next(iter(self.store.get_reviews([proposal.review_id or ""])), None)
            report = check_reply(
                text, author=review.author if review else None, policy=Policy.parse(app.policy)
            )
            if not report.passed:
                raise ServiceError(422, "guardrail_failed", report.summary())
            return {**draft, "text": text, "edited": True}
        allowed = {"title", "summary", "suspected_area", "severity", "body"}
        unknown = set(edits) - allowed
        if unknown:
            raise ServiceError(422, "bad_edit", f"issue edits may change {sorted(allowed)} only")
        edited_body = edits.get("body")
        merged = {**draft, **{k: v for k, v in edits.items() if k != "body"}}
        issue = IssueDraft.model_validate(merged)
        members = set(self.store.theme_review_ids(issue.theme_id))
        reviews = {r.id: r for r in self.store.get_reviews(list(members))}
        flags = {rid: s.flags for rid, s in self.store.get_signals(list(members)).items()}
        report = check_issue(
            issue.model_dump(), theme_review_ids=members, reviews=reviews, flags=flags
        )
        if not report.passed:
            raise ServiceError(422, "guardrail_failed", report.summary())
        payload = issue.model_dump()
        # An operator-written body is used as written (the operator is trusted; the
        # evidence checks above still ran on the structured fields).
        payload["body"] = (
            str(edited_body).strip()
            if edited_body
            else render_issue_body(issue, app_name=app.name, proposal_id=proposal.id)
        )
        if not payload["body"]:
            raise ServiceError(422, "bad_edit", "the issue body cannot be empty")
        payload["edited"] = True
        return payload

    def _token(self, app: App) -> str:
        settings = self.engine.settings
        if app.github_token_ciphertext:
            try:
                return decrypt_secret(
                    settings.secret(settings.app_encryption_key),
                    app.id,
                    app.github_token_ciphertext,
                )
            except SecretboxError as exc:
                raise ServiceError(422, "github_token_unreadable", str(exc)) from exc
        token = settings.secret(settings.github_token)
        if not token:
            raise ServiceError(
                422, "no_github_token", "set a token on the app or GITHUB_TOKEN on the server"
            )
        return token

    def approve(
        self, proposal_id: str, *, edits: dict[str, Any] | None = None, operator: str = "operator"
    ) -> Proposal:
        proposal = self._get(proposal_id)
        if proposal.status == "executed" or (
            proposal.status == "approved" and proposal.kind == "reply"
        ):
            return proposal  # idempotent: approving twice changes nothing
        if proposal.status == "rejected":
            raise ServiceError(409, "proposal_rejected", "a rejected proposal cannot be approved")
        app = self.store.get_app(proposal.app_id)
        if app is None:
            raise ServiceError(404, "app_not_found", "app not found")
        draft = self._edited_draft(proposal, app, edits) if edits else proposal.draft
        token = None
        if proposal.kind == "issue":
            if not app.github_repo:
                raise ServiceError(422, "no_github_repo", "connect a GitHub repo to this app")
            token = self._token(app)  # fail before changing state

        claimed = self.store.transition_proposal(
            proposal.id,
            ("proposed", "failed"),
            status="approved",
            draft=draft,
            decided_by=operator,
            decided_at=utcnow(),
        )
        if claimed is None:  # lost a race: someone else approved or rejected it just now
            return self._get(proposal.id)
        audit(
            self.store,
            "operator",
            "proposal.approved",
            app_id=app.id,
            entity_type="proposal",
            entity_id=proposal.id,
            input={"edited": bool(edits)},
        )
        if proposal.kind == "reply":
            return claimed

        # The executor acts on the stored draft, never on anything the model says now.
        assert token is not None and app.github_repo
        try:
            created = self.engine.github_builder(token).create_issue(
                app.github_repo,
                claimed.draft["title"],
                claimed.draft["body"],
                claimed.draft.get("labels") or ["review-radar"],
            )
        except GitHubIssueError as exc:
            failed = self.store.transition_proposal(
                proposal.id,
                ("approved",),
                status="failed",
                result={"error": exc.code, "message": exc.message},
            )
            audit(
                self.store,
                "system",
                "issue.failed",
                app_id=app.id,
                entity_type="proposal",
                entity_id=proposal.id,
                output={"error": exc.code},
            )
            return failed or self._get(proposal.id)
        executed = self.store.transition_proposal(
            proposal.id,
            ("approved",),
            status="executed",
            result={"url": created.url, "number": created.number, "repo": app.github_repo},
        )
        audit(
            self.store,
            "system",
            "issue.created",
            app_id=app.id,
            entity_type="proposal",
            entity_id=proposal.id,
            output={"url": created.url, "number": created.number},
        )
        return executed or self._get(proposal.id)

    def reject(self, proposal_id: str, *, reason: str, operator: str = "operator") -> Proposal:
        proposal = self._get(proposal_id)
        if proposal.status == "rejected":
            return proposal
        if proposal.status in ("approved", "executed"):
            raise ServiceError(409, "proposal_decided", f"proposal is already {proposal.status}")
        rejected = self.store.transition_proposal(
            proposal.id,
            ("proposed", "failed"),
            status="rejected",
            decided_by=operator,
            decided_at=utcnow(),
            decision_reason=reason.strip()[:500] or "rejected",
        )
        if rejected is None:
            return self._get(proposal.id)
        audit(
            self.store,
            "operator",
            "proposal.rejected",
            app_id=proposal.app_id,
            entity_type="proposal",
            entity_id=proposal.id,
            input={"reason": rejected.decision_reason},
        )
        return rejected
