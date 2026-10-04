"""Domain models: the rows of spec section 5 as Pydantic objects, plus run usage.

Two kinds of fields live side by side and are kept apart on purpose:

  - **store facts** (rating, app version, date, the review text): what the feed said. They
    are never rewritten by a model.
  - **model signals** (category, sentiment, severity, device mentions): what a model
    *read into* the text. They live in `Signals`, which records the model and the prompt
    version that produced them, so a UI can label them as inferred and an eval can trace a
    regression to a prompt change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Store = Literal["ios", "android"]
Category = Literal["bug", "request", "praise", "billing", "performance", "other"]
Sentiment = Literal["positive", "neutral", "negative", "mixed"]
ThemeKind = Literal["bug", "request", "praise", "billing", "other"]
ThemeStatus = Literal["open", "resolved", "ignored"]
ProposalKind = Literal["reply", "issue"]
ProposalStatus = Literal["proposed", "approved", "rejected", "executed", "failed"]
RunStatus = Literal[
    "queued", "fetching", "extracting", "embedding", "clustering", "proposing", "done", "failed"
]
StepKind = Literal["tool_call", "tool_result", "model", "note"]

TERMINAL_RUN_STATUSES = frozenset({"done", "failed"})
CATEGORIES: tuple[str, ...] = ("bug", "request", "praise", "billing", "performance", "other")
SENTIMENTS: tuple[str, ...] = ("positive", "neutral", "negative", "mixed")


def utcnow() -> datetime:
    return datetime.now(UTC)


def theme_kind_for(category: str) -> ThemeKind:
    """Performance problems are filed as bugs; everything else maps one to one."""
    if category in ("bug", "performance"):
        return "bug"
    if category in ("request", "praise", "billing"):
        return category  # type: ignore[return-value]
    return "other"


class App(BaseModel):
    id: str
    store: Store
    store_id: str
    name: str
    country: str = "us"
    github_repo: str | None = None
    policy: str = ""
    public: bool = False
    github_token_ciphertext: str | None = None
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def has_github_token(self) -> bool:
        return bool(self.github_token_ciphertext)


class Review(BaseModel):
    id: str
    app_id: str
    store_review_id: str
    source: Literal["appstore", "csv", "googleplay"] = "appstore"
    author: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    title: str = ""
    body: str
    app_version: str | None = None
    date: datetime | None = None
    fetched_at: datetime = Field(default_factory=utcnow)
    embedding: list[float] | None = None

    @property
    def text(self) -> str:
        return f"{self.title}. {self.body}" if self.title else self.body


class Signals(BaseModel):
    review_id: str
    category: Category
    sentiment: Sentiment
    severity: int = Field(ge=1, le=5)
    feature_area: str | None = None
    devices: list[str] = Field(default_factory=list)
    os_versions: list[str] = Field(default_factory=list)
    app_versions: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    # Deterministic flags set by code, never by the model (e.g. "prompt_injection").
    flags: list[str] = Field(default_factory=list)
    model: str
    prompt_version: str
    created_at: datetime = Field(default_factory=utcnow)


class ThemeQuote(BaseModel):
    review_id: str
    text: str


class Theme(BaseModel):
    id: str
    app_id: str
    title: str
    summary: str
    kind: ThemeKind
    status: ThemeStatus = "open"
    review_count: int = 0
    quotes: list[ThemeQuote] = Field(default_factory=list)
    embedding: list[float] | None = None
    first_run_id: str | None = None
    last_run_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Proposal(BaseModel):
    id: str
    app_id: str
    run_id: str | None = None
    kind: ProposalKind
    theme_id: str | None = None
    review_id: str | None = None
    draft: dict[str, Any]
    reasoning: str = ""
    guardrails: dict[str, Any] = Field(default_factory=dict)
    status: ProposalStatus = "proposed"
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Run(BaseModel):
    id: str
    app_id: str
    status: RunStatus = "queued"
    trigger: Literal["manual", "cron", "demo", "eval"] = "manual"
    budget: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    error: str | None = None
    client_key: str | None = None
    attempts: int = 0
    lease_until: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RunStep(BaseModel):
    """One line of the trajectory (the flight recorder). Append-only."""

    run_id: str
    seq: int
    at: datetime = Field(default_factory=utcnow)
    stage: str
    kind: StepKind
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] | None = None


class AgentEvent(BaseModel):
    """Append-only audit: who did what to which entity (operator, agent, system, cron)."""

    id: str
    app_id: str | None = None
    actor: Literal["agent", "operator", "system", "cron"]
    type: str
    entity_type: str | None = None
    entity_id: str | None = None
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    model: str | None = None
    prompt_version: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


class MemoryHit(BaseModel):
    """A search_memory result: a past theme or proposal, with the human decision if any."""

    kind: Literal["theme", "proposal"]
    id: str
    title: str
    snippet: str
    status: str
    score: float
    theme_id: str | None = None
    decision_reason: str | None = None


class ModelUsage(BaseModel):
    model: str
    provider: str
    stage: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    usd: float = 0.0


class Usage(BaseModel):
    """The run's ledger, summarised for the UI and the `runs.usage` column."""

    calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    usd: float = 0.0
    max_usd: float | None = None
    by_model: list[ModelUsage] = Field(default_factory=list)
    summary: str = ""
    prices_verified_on: str = ""

    @classmethod
    def from_ledger(cls, ledger: Any, max_usd: float | None = None) -> Usage:
        from llm_kit import PRICES_VERIFIED_ON

        rows: dict[tuple[str, str], ModelUsage] = {}
        for record in ledger.records:
            stage = record.label.split(":", 1)[0]
            row = rows.setdefault(
                (record.model, stage),
                ModelUsage(model=record.model, provider=record.provider, stage=stage),
            )
            row.calls += 1
            row.prompt_tokens += record.usage.prompt_tokens
            row.completion_tokens += record.usage.completion_tokens
            row.reasoning_tokens += record.usage.reasoning_tokens
            row.usd = round(row.usd + record.cost_usd, 8)
        total = ledger.total_usage
        return cls(
            calls=len(ledger.records),
            failed_calls=sum(1 for record in ledger.records if record.error),
            prompt_tokens=total.prompt_tokens,
            completion_tokens=total.completion_tokens,
            reasoning_tokens=total.reasoning_tokens,
            total_tokens=total.total_tokens,
            usd=round(ledger.total_usd, 8),
            max_usd=max_usd,
            by_model=list(rows.values()),
            summary=ledger.summary(),
            prices_verified_on=PRICES_VERIFIED_ON,
        )
