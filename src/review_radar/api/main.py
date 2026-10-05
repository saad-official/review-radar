"""The HTTP API (spec section 6). OpenAPI at /api/docs.

    uvicorn review_radar.api.main:app --port 7860     (local)
    main.py at the repository root re-exports `app`    (Vercel's Python runtime)

Auth (no accounts in the MVP): write routes need `Authorization: Bearer $OPERATOR_TOKEN`;
read routes are public for apps marked `public` (the demo app) and need the operator token
otherwise. `/api/cron/daily` takes `Bearer $CRON_SECRET` (Vercel Cron sends it).

Every error is `{"detail": {"code", "message", "retry_after"?}}`. `create_app(engine)`
builds the app around any Engine, which is how the tests run the real routes against an
in-memory store, a fake model and a fake GitHub.
"""

import asyncio
import hmac
import json
import time
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from .. import __version__
from ..config import get_settings
from ..db import MemoryStore, Store
from ..dispatch import dispatch_mode, process_url, publish_to_qstash, verify_qstash_signature
from ..engine import Engine
from ..executors.export import replies_csv
from ..ingest.base import IngestError
from ..ingest.csv import parse_csv
from ..models import TERMINAL_RUN_STATUSES, App, Proposal, Run, RunStep, utcnow
from ..observability import configure_logging
from ..service import (
    ProposalService,
    RunService,
    ServiceError,
    audit,
    import_reviews,
    seal_token,
)
from ..service import (
    create_app as create_app_record,
)
from .ratelimit import TokenBucket, client_ip, client_key
from .schemas import (
    AppCreate,
    ApproveBody,
    AppUpdate,
    AppView,
    ErrorBody,
    EventView,
    Health,
    ImportResult,
    ProcessResult,
    ProposalView,
    RejectBody,
    ReviewBrief,
    ReviewView,
    RunCreate,
    RunCreated,
    RunSummary,
    RunView,
    SentimentPoint,
    SignalsView,
    StepView,
    ThemeBrief,
    ThemeView,
)

SSE_POLL_S = 0.5
SSE_MAX_S = 250.0  # under Vercel's 300 s; EventSource reconnects with Last-Event-ID
SSE_KEEPALIVE_S = 15.0
ACTIVE_RUN_WINDOW = timedelta(minutes=30)
ERRORS = {
    400: {"model": ErrorBody},
    401: {"model": ErrorBody},
    404: {"model": ErrorBody},
    409: {"model": ErrorBody},
    422: {"model": ErrorBody},
}


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    body = ErrorBody(code=code, message=message, **extra)
    headers = {"Retry-After": str(body.retry_after)} if body.retry_after else None
    return HTTPException(
        status_code=status, detail=body.model_dump(mode="json", exclude_none=True), headers=headers
    )


class UTF8JSONResponse(JSONResponse):
    """JSON is UTF-8 by spec, but clients that ignore the spec (Windows PowerShell 5.1's
    Invoke-RestMethod, some proxies and CSV tools) fall back to Latin-1/cp1252 when the
    Content-Type carries no charset and show "I’m" as "Iâ€™m". Saying it costs nothing."""

    media_type = "application/json; charset=utf-8"


def _bearer_matches(authorization: str | None, secret: str) -> bool:
    if not secret or not authorization:
        return False
    return hmac.compare_digest(authorization.encode(), f"Bearer {secret}".encode())


def create_app(engine: Engine | None = None) -> FastAPI:
    settings = engine.settings if engine else get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Review Radar API",
        version=__version__,
        description=(
            "App-store reviews in; themes, reply drafts and GitHub issue proposals out. Every "
            "write the agent makes is a proposal that waits for a human. Write routes need "
            "`Authorization: Bearer <OPERATOR_TOKEN>`."
        ),
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        default_response_class=UTF8JSONResponse,
    )
    app.state.engine = engine
    app.state.bucket = TokenBucket(settings.runs_per_hour_per_ip, 3600.0)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.web_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Last-Event-ID"],
        expose_headers=["Retry-After", "Content-Disposition"],
        max_age=600,
    )

    def get_engine() -> Engine:
        if app.state.engine is None:
            app.state.engine = Engine.from_settings(settings)
        return app.state.engine

    EngineDep = Annotated[Engine, Depends(get_engine)]
    AuthHeader = Annotated[str | None, Header()]

    def is_operator(engine: Engine, authorization: str | None) -> bool:
        return _bearer_matches(
            authorization, engine.settings.secret(engine.settings.operator_token)
        )

    def require_operator(engine: Engine, authorization: str | None) -> None:
        if not engine.settings.secret(engine.settings.operator_token):
            raise _error(503, "operator_auth_disabled", "OPERATOR_TOKEN is not configured")
        if not is_operator(engine, authorization):
            raise _error(401, "unauthorized", "missing or wrong operator token")

    def readable_app(engine: Engine, app_id: str, authorization: str | None) -> App:
        try:
            record = engine.store.get_app(app_id)
        except Exception as exc:  # invalid uuid text in Postgres
            raise _error(404, "app_not_found", "app not found") from exc
        if record is None:
            raise _error(404, "app_not_found", "app not found")
        if not record.public and not is_operator(engine, authorization):
            raise _error(401, "unauthorized", "this app is private; send the operator token")
        return record

    def readable_run(engine: Engine, run_id: str, authorization: str | None) -> Run:
        try:
            run = engine.store.get_run(run_id)
        except Exception as exc:
            raise _error(404, "run_not_found", "run not found") from exc
        if run is None:
            raise _error(404, "run_not_found", "run not found")
        readable_app(engine, run.app_id, authorization)
        return run

    def undecided(engine: Engine, proposal_id: str) -> None:
        """A repeat decision answers 409 (the service itself is idempotent underneath, so a
        retried request can never create a second GitHub issue)."""
        try:
            proposal = engine.store.get_proposal(proposal_id)
        except Exception:
            proposal = None
        if proposal is None:
            raise _error(404, "proposal_not_found", "proposal not found")
        if proposal.status not in ("proposed", "failed"):
            raise _error(409, "already_decided", f"proposal is already {proposal.status}")

    def service_error(exc: ServiceError) -> HTTPException:
        return _error(exc.status, exc.code, exc.message)

    def app_view(engine: Engine, record: App) -> AppView:
        runs = engine.store.list_runs(record.id, limit=1)
        last = runs[0] if runs else None
        return AppView(
            id=record.id,
            store=record.store,
            store_id=record.store_id,
            name=record.name,
            country=record.country,
            github_repo=record.github_repo,
            policy=record.policy,
            public=record.public,
            has_github_token=record.has_github_token,
            created_at=record.created_at,
            counts=engine.store.app_counts(record.id),
            new_reviews=int((last.stats or {}).get("new_reviews", 0)) if last else 0,
            last_run=run_summary(last) if last else None,
        )

    def proposal_view(engine: Engine, proposal: Proposal) -> ProposalView:
        review = theme = None
        if proposal.review_id:
            found = engine.store.get_reviews([proposal.review_id])
            if found:
                r = found[0]
                review = ReviewBrief(
                    id=r.id,
                    store_review_id=r.store_review_id,
                    rating=r.rating,
                    title=r.title,
                    body=r.body,
                    app_version=r.app_version,
                    date=r.date,
                )
        if proposal.theme_id:
            t = engine.store.get_theme(proposal.theme_id)
            if t:
                theme = ThemeBrief(id=t.id, title=t.title, kind=t.kind, review_count=t.review_count)
        data = proposal.model_dump()
        if proposal.kind == "issue":
            draft = dict(data["draft"])
            draft.setdefault("devices", draft.get("affected_devices", []))
            data["draft"] = draft
        return ProposalView(**data, reason=proposal.decision_reason, review=review, theme=theme)

    # ------------------------------------------------------------------ meta

    @app.get("/api/health", response_model=Health, tags=["meta"])
    def health(engine: EngineDep) -> Health:
        s = engine.settings
        return Health(
            ok=True,
            version=__version__,
            providers={name: s.has_key(name) for name in ("groq", "gemini", "voyage")},
            embeddings=s.embedding_provider
            if (s.embedding_provider == "hash" or s.has_key(s.embedding_provider))
            else "none",
            db=engine.store.ping(),
            store="memory" if isinstance(engine.store, MemoryStore) else "postgres",
            dispatch=dispatch_mode(s),
            operator_auth=bool(s.secret(s.operator_token)),
            encryption=bool(s.secret(s.app_encryption_key)),
        )

    # ------------------------------------------------------------------ apps

    @app.get("/api/apps", response_model=list[AppView], tags=["apps"])
    def list_apps(engine: EngineDep, authorization: AuthHeader = None) -> list[AppView]:
        operator = is_operator(engine, authorization)
        return [app_view(engine, a) for a in engine.store.list_apps() if a.public or operator]

    @app.post("/api/apps", status_code=201, response_model=AppView, tags=["apps"], responses=ERRORS)
    def add_app(body: AppCreate, engine: EngineDep, authorization: AuthHeader = None) -> AppView:
        require_operator(engine, authorization)
        name = body.name
        if not name and body.store == "ios":
            from ..ingest.appstore import lookup_app_name

            name = lookup_app_name(body.store_id, body.country)
        try:
            record = create_app_record(
                engine,
                store_kind=body.store,
                store_id=body.store_id,
                name=name or body.store_id,
                country=body.country,
                github_repo=body.github_repo,
                policy=body.policy,
                public=body.public,
                github_token=body.github_token.get_secret_value() if body.github_token else None,
            )
        except ServiceError as exc:
            raise service_error(exc) from exc
        return app_view(engine, record)

    @app.get("/api/apps/{app_id}", response_model=AppView, tags=["apps"], responses=ERRORS)
    def get_app(app_id: str, engine: EngineDep, authorization: AuthHeader = None) -> AppView:
        return app_view(engine, readable_app(engine, app_id, authorization))

    @app.patch("/api/apps/{app_id}", response_model=AppView, tags=["apps"], responses=ERRORS)
    def update_app(
        app_id: str, body: AppUpdate, engine: EngineDep, authorization: AuthHeader = None
    ) -> AppView:
        require_operator(engine, authorization)
        record = readable_app(engine, app_id, authorization)
        fields = body.model_dump(exclude_unset=True, exclude={"github_token"})
        if body.github_token is not None:
            token = body.github_token.get_secret_value()
            try:
                fields["github_token_ciphertext"] = (
                    seal_token(engine, record.id, token) if token else None
                )
            except ServiceError as exc:
                raise service_error(exc) from exc
        if fields:
            engine.store.update_app(record.id, **fields)
            audit(
                engine.store,
                "operator",
                "app.updated",
                app_id=record.id,
                entity_type="app",
                entity_id=record.id,
                input={k: ("<set>" if "token" in k else v) for k, v in fields.items()},
            )
        return app_view(engine, readable_app(engine, app_id, authorization))

    @app.delete("/api/apps/{app_id}", status_code=204, tags=["apps"], responses=ERRORS)
    def delete_app(app_id: str, engine: EngineDep, authorization: AuthHeader = None) -> None:
        require_operator(engine, authorization)
        readable_app(engine, app_id, authorization)
        engine.store.delete_app(app_id)

    @app.post(
        "/api/apps/{app_id}/import", response_model=ImportResult, tags=["apps"], responses=ERRORS
    )
    async def import_csv(
        app_id: str, request: Request, engine: EngineDep, authorization: AuthHeader = None
    ) -> ImportResult:
        """Multipart upload with the file in field `file` (what the web UI sends), or the CSV
        itself as the body (`Content-Type: text/csv`). Column schema in docs/api.md."""
        require_operator(engine, authorization)
        record = await run_in_threadpool(readable_app, engine, app_id, authorization)
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("file")
            if upload is None or isinstance(upload, str):
                raise _error(400, "missing_file", "send the CSV in the multipart field `file`")
            raw = await upload.read()
        else:
            raw = await request.body()
        try:
            parsed = parse_csv(raw)
        except IngestError as exc:
            raise _error(exc.status, exc.code, exc.message) from exc
        new_ids = await run_in_threadpool(import_reviews, engine, record, parsed.reviews)
        return ImportResult(
            rows=parsed.rows,
            imported=len(new_ids),
            skipped=parsed.rows - len(new_ids),
            duplicates=len(parsed.reviews) - len(new_ids),
            errors=[e.model_dump() for e in parsed.errors],
        )

    @app.get("/api/apps/{app_id}/events", response_model=list[EventView], tags=["apps"])
    def app_events(
        app_id: str,
        engine: EngineDep,
        authorization: AuthHeader = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[EventView]:
        readable_app(engine, app_id, authorization)
        return [EventView(**e.model_dump()) for e in engine.store.list_events(app_id, limit=limit)]

    # ------------------------------------------------------------------ runs

    def start_run(
        engine: EngineDep,
        record: App,
        *,
        trigger: str,
        key: str | None,
        max_reviews: int | None,
    ) -> Run:
        for existing in engine.store.list_runs(record.id, limit=5):
            if existing.status in TERMINAL_RUN_STATUSES:
                continue
            if utcnow() - existing.created_at < ACTIVE_RUN_WINDOW:
                raise _error(
                    409,
                    "run_active",
                    f"run {existing.id} is still in progress for this app; process or wait for it",
                )
            engine.store.update_run(
                existing.id, status="failed", error="abandoned", finished_at=utcnow()
            )
        return RunService(engine).create_run(
            record.id, trigger=trigger, client_key=key, max_reviews=max_reviews
        )

    def process_until_done(engine: Engine, run_id: str, deadline: float) -> None:
        service = RunService(engine)
        while True:
            remaining = deadline - time.monotonic()
            if remaining < 30:
                return
            outcome = service.process(run_id, min(engine.settings.process_time_budget_s, remaining))
            if not outcome.resumable:
                return

    @app.post(
        "/api/apps/{app_id}/runs",
        status_code=202,
        response_model=RunCreated,
        tags=["runs"],
        responses={**ERRORS, 429: {"model": ErrorBody}},
    )
    def create_run(
        app_id: str,
        request: Request,
        background: BackgroundTasks,
        engine: EngineDep,
        body: RunCreate | None = None,
        authorization: AuthHeader = None,
    ) -> RunCreated:
        require_operator(engine, authorization)
        record = readable_app(engine, app_id, authorization)
        key = client_key(client_ip(request, engine.settings), engine.settings)
        allowed, wait_s = app.state.bucket.take(key)
        recent = engine.store.count_runs_since(key, utcnow() - timedelta(hours=1))
        if not allowed or recent >= engine.settings.runs_per_hour_per_ip:
            raise _error(
                429,
                "rate_limited",
                f"at most {engine.settings.runs_per_hour_per_ip} runs per hour",
                retry_after=max(1, int(wait_s) or 60),
            )
        run = start_run(
            engine,
            record,
            trigger="manual",
            key=key,
            max_reviews=body.max_reviews if body else None,
        )
        mode = dispatch_mode(engine.settings)
        if mode == "qstash" and not publish_to_qstash(engine.settings, run.id):
            mode = "client" if engine.settings.vercel else "background"
        if mode == "background":
            background.add_task(process_until_done, engine, run.id, time.monotonic() + 3600.0)
        return RunCreated(
            id=run.id,
            status=run.status,
            process=mode,
            process_url=f"/api/runs/{run.id}/process",
            events_url=f"/api/runs/{run.id}/events",
        )

    @app.get("/api/apps/{app_id}/runs", response_model=list[RunSummary], tags=["runs"])
    def list_runs(
        app_id: str,
        engine: EngineDep,
        authorization: AuthHeader = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[RunSummary]:
        readable_app(engine, app_id, authorization)
        return [run_summary(r) for r in engine.store.list_runs(app_id, limit=limit)]

    @app.post(
        "/api/runs/{run_id}/process",
        response_model=ProcessResult,
        tags=["runs"],
        responses={202: {"model": ProcessResult}, **ERRORS},
    )
    async def process_run(
        run_id: str,
        request: Request,
        engine: EngineDep,
        authorization: AuthHeader = None,
        upstash_signature: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        """Run or resume a queued run. 200 when finished (or already finished); 202 with
        `resumable: true` when the time budget ran out (call again); 409 `run_in_progress`
        while another request holds the run's lease (leases expire after ~270 s)."""
        if upstash_signature is not None:
            keys = [
                k.get_secret_value()
                for k in (
                    engine.settings.qstash_current_signing_key,
                    engine.settings.qstash_next_signing_key,
                )
                if k
            ]
            body = await request.body()
            if not keys or not verify_qstash_signature(
                upstash_signature, body, process_url(engine.settings, run_id), keys
            ):
                raise _error(401, "unauthorized", "QStash signature did not verify")
        else:
            require_operator(engine, authorization)
        try:
            exists = await run_in_threadpool(engine.store.get_run, run_id)
        except Exception:
            exists = None
        if exists is None:
            raise _error(404, "run_not_found", "run not found")
        outcome = await run_in_threadpool(
            RunService(engine).process, run_id, engine.settings.process_time_budget_s
        )
        if outcome.status == "in_progress":
            raise _error(409, "run_in_progress", "another request is processing this run")
        result = ProcessResult(
            id=run_id, status=outcome.status, resumable=outcome.resumable, message=outcome.message
        )
        return UTF8JSONResponse(result.model_dump(), status_code=202 if outcome.resumable else 200)

    @app.get("/api/runs/{run_id}", response_model=RunView, tags=["runs"], responses=ERRORS)
    def get_run(run_id: str, engine: EngineDep, authorization: AuthHeader = None) -> RunView:
        run = readable_run(engine, run_id, authorization)
        return RunView(**run.model_dump(exclude={"client_key", "lease_until"}))

    @app.get("/api/runs/{run_id}/steps", response_model=list[StepView], tags=["runs"])
    def get_steps(
        run_id: str,
        engine: EngineDep,
        authorization: AuthHeader = None,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> list[StepView]:
        """The trajectory: every stage, model turn, tool call and tool result, in order."""
        readable_run(engine, run_id, authorization)
        return [StepView(**s.model_dump()) for s in engine.store.list_steps(run_id, after)]

    @app.get(
        "/api/runs/{run_id}/events",
        tags=["runs"],
        responses={200: {"content": {"text/event-stream": {}}}, 404: {"model": ErrorBody}},
    )
    async def run_events(
        run_id: str,
        engine: EngineDep,
        authorization: AuthHeader = None,
        token: Annotated[
            str | None, Query(description="operator token (EventSource cannot set headers)")
        ] = None,
        last_event_id: Annotated[str | None, Header()] = None,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> StreamingResponse:
        """Server-Sent Events: replays the run's steps, then tails them until the run is done
        or failed. Each event is unnamed (`onmessage`), `data` is a StepView JSON object and
        `id` its `seq`, so a reconnecting EventSource resumes via Last-Event-ID."""
        auth = authorization or (f"Bearer {token}" if token else None)
        await run_in_threadpool(readable_run, engine, run_id, auth)
        start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after
        return StreamingResponse(
            _event_stream(engine.store, run_id, start),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------ themes and reviews

    @app.get("/api/apps/{app_id}/themes", response_model=list[ThemeView], tags=["themes"])
    def list_themes(
        app_id: str,
        engine: EngineDep,
        authorization: AuthHeader = None,
        status: Annotated[str | None, Query(pattern="^(open|resolved|ignored)$")] = None,
    ) -> list[ThemeView]:
        """Ranked by review count, with sentiment counts and a per-day sentiment series."""
        readable_app(engine, app_id, authorization)
        views = []
        for theme in engine.store.list_themes(app_id, status=status):
            member_ids = engine.store.theme_review_ids(theme.id)
            reviews = engine.store.get_reviews(member_ids)
            signals = engine.store.get_signals(member_ids)
            sentiment = Counter(s.sentiment for s in signals.values())
            by_day: dict[str, Counter] = defaultdict(Counter)
            for review in reviews:
                if review.date and review.id in signals:
                    by_day[review.date.date().isoformat()][signals[review.id].sentiment] += 1
            ratings = [r.rating for r in reviews if r.rating]
            issue = engine.store.find_proposal("issue", theme_id=theme.id)
            views.append(
                ThemeView(
                    **theme.model_dump(
                        exclude={"embedding", "app_id", "first_run_id", "last_run_id"}
                    ),
                    sentiment={
                        k: sentiment.get(k, 0) for k in ("positive", "neutral", "negative", "mixed")
                    },
                    sentiment_by_day=[
                        SentimentPoint(date=day, **dict(counts))
                        for day, counts in sorted(by_day.items())
                    ],
                    max_severity=max((s.severity for s in signals.values()), default=0),
                    avg_rating=round(sum(ratings) / len(ratings), 2) if ratings else None,
                    issue_proposal=(
                        {
                            "id": issue.id,
                            "status": issue.status,
                            "url": (issue.result or {}).get("url"),
                        }
                        if issue
                        else None
                    ),
                )
            )
        return views

    @app.get("/api/apps/{app_id}/reviews", response_model=list[ReviewView], tags=["themes"])
    def list_reviews(
        app_id: str,
        engine: EngineDep,
        authorization: AuthHeader = None,
        theme: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[ReviewView]:
        readable_app(engine, app_id, authorization)
        try:
            reviews = engine.store.list_reviews(app_id, theme_id=theme, limit=limit, offset=offset)
        except Exception as exc:
            raise _error(404, "theme_not_found", "theme not found") from exc
        ids = [r.id for r in reviews]
        signals = engine.store.get_signals(ids)
        themes = engine.store.review_theme_ids(ids)
        return [
            ReviewView(
                **r.model_dump(exclude={"embedding", "app_id", "fetched_at"}),
                signals=SignalsView(**signals[r.id].model_dump()) if r.id in signals else None,
                theme_ids=themes.get(r.id, []),
            )
            for r in reviews
        ]

    # ------------------------------------------------------------------ proposals

    @app.get("/api/apps/{app_id}/proposals", response_model=list[ProposalView], tags=["proposals"])
    def list_proposals(
        app_id: str,
        engine: EngineDep,
        authorization: AuthHeader = None,
        status: Annotated[
            str | None, Query(pattern="^(proposed|approved|rejected|executed|failed)$")
        ] = None,
        kind: Annotated[str | None, Query(pattern="^(reply|issue)$")] = None,
        run: str | None = None,
    ) -> list[ProposalView]:
        readable_app(engine, app_id, authorization)
        return [
            proposal_view(engine, p)
            for p in engine.store.list_proposals(app_id, status=status, kind=kind, run_id=run)
        ]

    @app.get(
        "/api/proposals/export.csv",
        response_class=PlainTextResponse,
        tags=["proposals"],
        responses={200: {"content": {"text/csv": {}}}, **ERRORS},
    )
    def export_replies(
        engine: EngineDep,
        app_id: Annotated[str, Query(alias="app")],
        authorization: AuthHeader = None,
    ) -> PlainTextResponse:
        """Approved replies as CSV, to paste into App Store Connect or the Play Console."""
        record = readable_app(engine, app_id, authorization)
        proposals = [
            p
            for p in engine.store.list_proposals(record.id, kind="reply")
            if p.status in ("approved", "executed")
        ]
        reviews = {
            r.id: r for r in engine.store.get_reviews([p.review_id or "" for p in proposals])
        }
        name = f"review-radar-replies-{record.store_id}.csv"
        return PlainTextResponse(
            replies_csv(proposals, reviews),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post(
        "/api/proposals/{proposal_id}/approve",
        response_model=ProposalView,
        tags=["proposals"],
        responses=ERRORS,
    )
    def approve(
        proposal_id: str,
        engine: EngineDep,
        body: ApproveBody | None = None,
        authorization: AuthHeader = None,
    ) -> ProposalView:
        """Approve (optionally with edits, re-checked by the guardrails). For an issue this
        creates it on GitHub; the result (url or error) is on the returned proposal.
        Idempotent: approving an executed proposal returns it unchanged."""
        require_operator(engine, authorization)
        undecided(engine, proposal_id)
        try:
            proposal = ProposalService(engine).approve(
                proposal_id, edits=body.changes() if body else None
            )
        except ServiceError as exc:
            raise service_error(exc) from exc
        return proposal_view(engine, proposal)

    @app.post(
        "/api/proposals/{proposal_id}/reject",
        response_model=ProposalView,
        tags=["proposals"],
        responses=ERRORS,
    )
    def reject(
        proposal_id: str, body: RejectBody, engine: EngineDep, authorization: AuthHeader = None
    ) -> ProposalView:
        """Reject with a reason. The reason is memory: `search_memory` shows it to the agent
        on later runs, and the theme can never get a second issue proposal."""
        require_operator(engine, authorization)
        undecided(engine, proposal_id)
        try:
            proposal = ProposalService(engine).reject(proposal_id, reason=body.reason)
        except ServiceError as exc:
            raise service_error(exc) from exc
        return proposal_view(engine, proposal)

    # ------------------------------------------------------------------ cron

    @app.post("/api/cron/daily", tags=["cron"])
    def cron_daily(engine: EngineDep, authorization: AuthHeader = None) -> dict[str, Any]:
        """For each app: resume its unfinished run or start one, and process within this
        request's time budget. Unfinished runs stay resumable (next cron or the UI)."""
        secret = engine.settings.secret(engine.settings.cron_secret)
        if not secret:
            raise _error(503, "cron_disabled", "CRON_SECRET is not configured")
        if not _bearer_matches(authorization, secret):
            raise _error(401, "unauthorized", "missing or wrong bearer token")
        deadline = time.monotonic() + engine.settings.process_time_budget_s
        results = []
        for record in engine.store.list_apps():
            if deadline - time.monotonic() < 45:
                results.append({"app_id": record.id, "status": "skipped", "reason": "time"})
                continue
            active = next(
                (
                    r
                    for r in engine.store.list_runs(record.id, limit=5)
                    if r.status not in TERMINAL_RUN_STATUSES
                ),
                None,
            )
            run = active or RunService(engine).create_run(record.id, trigger="cron")
            outcome = RunService(engine).process(run.id, deadline - time.monotonic() - 10)
            results.append(
                {
                    "app_id": record.id,
                    "run_id": run.id,
                    "status": outcome.status,
                    "resumable": outcome.resumable,
                }
            )
        return {"apps": results}

    # Vercel Cron sends GET with the bearer: same handler, documented once.
    app.add_api_route("/api/cron/daily", cron_daily, methods=["GET"], include_in_schema=False)

    return app


def run_summary(run: Run) -> RunSummary:
    return RunSummary(
        id=run.id,
        status=run.status,
        trigger=run.trigger,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        summary=run.summary,
        usd=(run.usage or {}).get("usd"),
        new_reviews=(run.stats or {}).get("new_reviews"),
    )


def sse_payload(step: RunStep) -> dict[str, Any]:
    """A StepView plus `data` (the result, with `status` on stage changes). The run's final
    status step is sent with `kind` "done" or "failed": the client's cue to close."""
    payload = StepView(**step.model_dump()).model_dump(mode="json")
    payload["data"] = dict(payload["result"])
    status = payload["result"].get("status") if step.name == "status" else None
    if status in TERMINAL_RUN_STATUSES:
        payload["kind"] = status
    return payload


def sse_message(payload: dict[str, Any], seq: int) -> str:
    return f"id: {seq}\ndata: {json.dumps(payload)}\n\n"


async def _event_stream(store: Store, run_id: str, after_seq: int):
    loop = asyncio.get_running_loop()
    started = last_ping = loop.time()
    seq = after_seq
    ended = False
    yield "retry: 2000\n\n"
    while True:
        for step in await run_in_threadpool(store.list_steps, run_id, seq):
            seq = step.seq
            payload = sse_payload(step)
            ended = ended or payload["kind"] in TERMINAL_RUN_STATUSES
            yield sse_message(payload, step.seq)
        run = await run_in_threadpool(store.get_run, run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            # One last read so the final status step is never lost to a race.
            for step in await run_in_threadpool(store.list_steps, run_id, seq):
                seq = step.seq
                payload = sse_payload(step)
                ended = ended or payload["kind"] in TERMINAL_RUN_STATUSES
                yield sse_message(payload, step.seq)
            if not ended:  # finished without a final status step (e.g. deleted mid-run)
                status = run.status if run else "error"
                yield sse_message(
                    {
                        "seq": seq + 1,
                        "stage": "finish",
                        "kind": status if status in TERMINAL_RUN_STATUSES else "error",
                        "name": "status",
                        "args": {},
                        "result": {"status": status},
                        "data": {"status": status, "error": run.error if run else "run not found"},
                        "usage": None,
                    },
                    seq + 1,
                )
            return
        now = loop.time()
        if now - started > SSE_MAX_S:
            return
        if now - last_ping > SSE_KEEPALIVE_S:
            last_ping = now
            yield ": keepalive\n\n"
        await asyncio.sleep(SSE_POLL_S)


app = create_app()
