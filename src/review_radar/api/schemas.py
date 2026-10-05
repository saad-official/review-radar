"""Request and response models for the HTTP API (spec section 6). The contract the web app
codes against; OpenAPI at /api/docs is generated from these."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from ..executors.github import REPO
from ..models import (
    ProposalKind,
    ProposalStatus,
    RunStatus,
    Store,
    ThemeKind,
    ThemeQuote,
    ThemeStatus,
)


def _repo(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    value = value.strip().removeprefix("https://github.com/").strip("/")
    if not REPO.match(value):
        raise ValueError('github_repo must be "owner/name"')
    return value


class AppCreate(BaseModel):
    store: Store
    store_id: str = Field(min_length=1, max_length=200, examples=["324684580"])
    country: str = Field(default="us", pattern=r"^[a-zA-Z]{2}$")
    name: str | None = Field(default=None, max_length=120)
    github_repo: str | None = Field(
        default=None, examples=["saad-official/review-radar-demo-issues"]
    )
    policy: str = Field(default="", max_length=4000)
    public: bool = False
    github_token: SecretStr | None = Field(
        default=None,
        description="Fine-grained PAT (Issues: write). Stored encrypted, never returned.",
    )

    @field_validator("github_repo")
    @classmethod
    def _check_repo(cls, value: str | None) -> str | None:
        return _repo(value)

    @field_validator("country")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()


class AppUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    github_repo: str | None = None
    policy: str | None = Field(default=None, max_length=4000)
    public: bool | None = None
    github_token: SecretStr | None = Field(
        default=None, description='A new token, or "" to remove the stored one.'
    )

    @field_validator("github_repo")
    @classmethod
    def _check_repo(cls, value: str | None) -> str | None:
        return _repo(value)


class RunSummary(BaseModel):
    id: str
    status: RunStatus
    trigger: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: str | None
    usd: float | None = None
    new_reviews: int | None = Field(default=None, description="reviews fetched by this run")


class AppView(BaseModel):
    id: str
    store: Store
    store_id: str
    name: str
    country: str
    github_repo: str | None
    policy: str
    public: bool
    has_github_token: bool
    created_at: datetime
    counts: dict[str, Any] = Field(description="reviews, analysed, themes, proposals: {status: n}")
    # Flat aliases of `counts` for the web UI (spec §6 App shape).
    review_count: int = 0
    theme_count: int = 0
    proposals_waiting: int = 0
    new_reviews: int = Field(default=0, description="new reviews fetched by the latest run")
    last_run: RunSummary | None = None

    @model_validator(mode="after")
    def _flatten_counts(self) -> AppView:
        counts = self.counts or {}
        self.review_count = int(counts.get("reviews") or 0)
        self.theme_count = int(counts.get("themes") or 0)
        proposals = counts.get("proposals") or {}
        self.proposals_waiting = (
            int(proposals.get("proposed") or 0) if isinstance(proposals, dict) else 0
        )
        return self


class RunCreate(BaseModel):
    max_reviews: int | None = Field(default=None, ge=1, le=200)


class RunCreated(BaseModel):
    id: str
    status: RunStatus
    process: Literal["qstash", "background", "client"] = Field(
        description='"client": POST process_url yourself (repeat while it answers 202), '
        "and follow events_url."
    )
    process_url: str
    events_url: str


class ProcessResult(BaseModel):
    id: str
    status: str
    resumable: bool = Field(description="True when the time budget ran out: POST again.")
    message: str = ""


class RunView(BaseModel):
    id: str
    app_id: str
    status: RunStatus
    trigger: str
    budget: dict[str, Any]
    usage: dict[str, Any] | None
    stats: dict[str, Any]
    summary: str | None
    error: str | None
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class StepView(BaseModel):
    seq: int
    at: datetime
    stage: str
    kind: Literal["tool_call", "tool_result", "model", "note"]
    name: str
    args: dict[str, Any]
    result: dict[str, Any]
    usage: dict[str, Any] | None


class SignalsView(BaseModel):
    """Model-inferred fields. `model` and `prompt_version` say who inferred them."""

    category: str
    sentiment: str
    severity: int
    feature_area: str | None
    devices: list[str]
    os_versions: list[str]
    app_versions: list[str]
    quotes: list[str]
    flags: list[str]
    model: str
    prompt_version: str


class ReviewView(BaseModel):
    id: str
    store_review_id: str
    source: str
    author: str | None
    rating: int | None
    title: str
    body: str
    app_version: str | None
    date: datetime | None
    signals: SignalsView | None
    theme_ids: list[str]


class SentimentPoint(BaseModel):
    date: str
    positive: int = 0
    neutral: int = 0
    negative: int = 0
    mixed: int = 0


class ThemeView(BaseModel):
    id: str
    title: str
    summary: str
    kind: ThemeKind
    status: ThemeStatus
    review_count: int
    quotes: list[ThemeQuote]
    sentiment: dict[str, int]
    sentiment_by_day: list[SentimentPoint]
    max_severity: int
    avg_rating: float | None
    issue_proposal: dict[str, Any] | None = Field(
        description="{id, status, url?} of this theme's issue proposal, if any"
    )
    created_at: datetime
    updated_at: datetime


class ReviewBrief(BaseModel):
    id: str
    store_review_id: str
    rating: int | None
    title: str
    body: str
    app_version: str | None
    date: datetime | None


class ThemeBrief(BaseModel):
    id: str
    title: str
    kind: ThemeKind
    review_count: int


REPLY_EVIDENCE_CHARS = 240


def _excerpt(text: str, limit: int = REPLY_EVIDENCE_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip(" ,.;:") + "…"


def reply_evidence(review: ReviewBrief) -> list[dict[str, Any]]:
    """A reply's evidence is the review it answers: same shape as an issue's evidence item."""
    quote = _excerpt(review.body or review.title or "")
    if not quote:
        return []
    return [
        {
            "review_id": review.id,
            "store_review_id": review.store_review_id,
            "quote": quote,
            "date": review.date.isoformat() if review.date else None,
            "rating": review.rating,
            "app_version": review.app_version,
        }
    ]


def guardrail_checks(guardrails: dict[str, Any]) -> list[dict[str, Any]]:
    """Stored checks are rule names (["length", "urls", ...]) plus a separate violations list;
    the view gives one `{name, ok, note}` object per rule. A violation whose rule is not in
    the stored list still shows up, as a failed check. Already-shaped objects pass through."""
    notes: dict[str, list[str]] = {}
    for violation in guardrails.get("violations") or []:
        if isinstance(violation, dict) and violation.get("rule"):
            notes.setdefault(str(violation["rule"]), []).append(str(violation.get("detail") or ""))
        elif isinstance(violation, str):
            notes.setdefault(violation, [])
    checks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in guardrails.get("checks") or []:
        if isinstance(item, dict) and "name" in item:
            name = str(item["name"])
            shaped = {"name": name, "ok": bool(item.get("ok")), "note": item.get("note")}
        else:
            name = str(item)
            note = "; ".join(n for n in notes.get(name, []) if n) or None
            shaped = {"name": name, "ok": name not in notes, "note": note}
        if name not in seen:
            seen.add(name)
            checks.append(shaped)
    for name, details in notes.items():
        if name not in seen:
            seen.add(name)
            note = "; ".join(d for d in details if d) or None
            checks.append({"name": name, "ok": False, "note": note})
    return checks


class ProposalView(BaseModel):
    id: str
    app_id: str
    run_id: str | None
    kind: ProposalKind
    status: ProposalStatus
    review_id: str | None = Field(default=None, description="reply: the review it answers")
    theme_id: str | None = Field(default=None, description="the proposal's theme, if any")
    draft: dict[str, Any] = Field(
        description="reply: {text, body (= text), review_alias, evidence[1]}; issue: {title, "
        "summary, suspected_area, severity, evidence[], affected_versions[], "
        "affected_devices[], devices[], body (rendered markdown)}. Evidence items: "
        "{review_id, store_review_id, quote, date, rating, app_version}"
    )
    reasoning: str
    guardrails: dict[str, Any] = Field(
        description="{passed, violations: [{rule, detail}], checks: [{name, ok, note}]}"
    )
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    reason: str | None = Field(description="the rejection reason (same as decision_reason)")
    result: dict[str, Any] | None = Field(
        description="issue: {url, number, repo} when executed, {error, message} when failed"
    )
    review: ReviewBrief | None
    theme: ThemeBrief | None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _web_shape(self) -> ProposalView:
        """Additive fields for the web UI (web/lib/api.ts): a reply's text also as `body`,
        a reply's evidence from its review, guardrail checks as objects. Stored data is not
        changed; every existing field keeps its value."""
        draft = dict(self.draft)
        if self.kind == "reply":
            if "text" in draft:
                draft["body"] = draft["text"]
            if not draft.get("evidence") and self.review is not None:
                draft["evidence"] = reply_evidence(self.review)
        self.draft = draft
        guardrails = dict(self.guardrails)
        guardrails["checks"] = guardrail_checks(guardrails)
        guardrails.setdefault("violations", [])
        guardrails.setdefault("passed", all(c["ok"] for c in guardrails["checks"]))
        self.guardrails = guardrails
        return self


class ApproveBody(BaseModel):
    """Send `draft` (or its synonym `edits`) only when the operator edited the proposal."""

    draft: dict[str, Any] | None = Field(
        default=None,
        description="reply: {text} or {body}; issue: any of {title, summary, body, "
        "suspected_area, severity}. Re-checked by the same guardrails.",
    )
    edits: dict[str, Any] | None = Field(default=None, description="synonym of `draft`")

    def changes(self) -> dict[str, Any] | None:
        return self.draft or self.edits or None


class RejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500, examples=["duplicate of #42"])


class ImportResult(BaseModel):
    rows: int
    imported: int
    skipped: int = Field(description="duplicates plus rows with errors")
    duplicates: int
    errors: list[dict[str, Any]] = Field(description="[{line, error}]")


class EventView(BaseModel):
    id: str
    actor: str
    type: str
    entity_type: str | None
    entity_id: str | None
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    created_at: datetime


class Health(BaseModel):
    ok: bool
    version: str
    providers: dict[str, bool]
    embeddings: str
    db: bool
    store: Literal["postgres", "memory"]
    dispatch: Literal["qstash", "background", "client"]
    operator_auth: bool
    encryption: bool


class ErrorBody(BaseModel):
    code: str
    message: str
    retry_after: int | None = Field(default=None, description="Seconds; also a header.")
    field: str | None = None
