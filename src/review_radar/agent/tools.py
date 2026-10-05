"""The propose loop's tools. The agent's entire write surface is `draft_reply` and
`propose_issue`, and both only ever insert a row with status `proposed`.

Design rules (decision 0001, docs/security.md):

  - **Arguments are validated by Pydantic** before a tool runs (llm-kit's `dispatch`), with
    lengths and ranges bounded, so a model influenced by a review cannot pass a 40,000
    character title or severity 9.
  - **Ids are aliases** (`R3`, `T2`) that this run handed to the model, never database
    ids. An id the model did not receive cannot resolve, so a hallucinated or injected id
    is a refused call, not a lookup.
  - **Every refusal is a ToolError in words the model can act on**: guardrail violations
    come back as a list so the model can fix its draft once; duplicates point at the
    existing proposal and the human decision.
  - **No tool can approve, execute, post, or create an issue.** Executors live in
    `executors/` and are imported only by the approve endpoint.
  - Tools are idempotent against the store (one reply proposal per review, one issue
    proposal per theme, enforced by unique indexes), which is what makes restarting the
    loop on a fallback provider safe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from llm_kit import Tool, ToolError
from pydantic import BaseModel, Field

from ..db.store import Store
from ..embeddings import EmbeddingProvider
from ..models import App, Proposal, Review, Signals, Theme
from .guardrails import Policy, Violation, check_issue, check_reply
from .injection import FLAG
from .templates import EvidenceQuote, IssueDraft, render_issue_body

ISSUE_KINDS = ("bug", "request", "billing")


class SearchMemoryArgs(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=200,
        description="What to look for, e.g. 'shuffle repeats songs' or 'login loop after update'.",
    )


class DraftReplyArgs(BaseModel):
    review_id: str = Field(max_length=12, description="A review id from this run, e.g. R3.")
    text: str = Field(
        min_length=1,
        max_length=1000,
        description="The reply, under 350 characters, following the reply policy.",
    )
    reasoning: str = Field(
        max_length=500, description="One or two sentences: why this review deserves a reply."
    )


class EvidenceArg(BaseModel):
    review_id: str = Field(max_length=12, description="A review id from the theme, e.g. R7.")
    quote: str = Field(
        min_length=3, max_length=300, description="A short excerpt copied verbatim from it."
    )


class ProposeIssueArgs(BaseModel):
    theme_id: str = Field(max_length=12, description="A theme id from this run, e.g. T2.")
    title: str = Field(min_length=8, max_length=120)
    summary: str = Field(min_length=10, max_length=800)
    suspected_area: str | None = Field(default=None, max_length=120)
    severity: int = Field(ge=1, le=5)
    evidence: list[EvidenceArg] = Field(min_length=1, max_length=6)
    reasoning: str = Field(max_length=600)


class SummariseRunArgs(BaseModel):
    summary: str = Field(min_length=10, max_length=1200)


@dataclass
class ToolContext:
    store: Store
    app: App
    run_id: str
    reviews: dict[str, Review]  # alias -> review
    signals: dict[str, Signals]  # review id -> signals
    themes: dict[str, Theme]  # alias -> theme
    theme_members: dict[str, list[str]]  # theme id -> review ids
    embedder: EmbeddingProvider | None = None
    max_replies: int = 10
    max_issues: int = 5
    replies: int = 0
    issues: int = 0
    reply_attempts: dict[str, int] = field(default_factory=dict)
    guardrail_checks: list[dict[str, Any]] = field(default_factory=list)
    summary: str | None = None
    created: list[str] = field(default_factory=list)

    @property
    def policy(self) -> Policy:
        return Policy.parse(self.app.policy)

    def alias_of_review(self, review_id: str) -> str | None:
        return next((a for a, r in self.reviews.items() if r.id == review_id), None)

    def alias_of_theme(self, theme_id: str | None) -> str | None:
        return next((a for a, t in self.themes.items() if t.id == theme_id), None)


def _json(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), default=str)


def search_memory(ctx: ToolContext, args: SearchMemoryArgs) -> str:
    vector = None
    if ctx.embedder is not None:
        try:
            vector = ctx.embedder.embed([args.query], label="memory", input_type="query")[0]
        except Exception:  # keyword half still works; a failed embed is not the model's fault
            vector = None
    hits = ctx.store.search_memory(ctx.app.id, args.query, vector, limit=6)
    return _json(
        {
            "results": [
                {
                    "kind": hit.kind,
                    "id": ctx.alias_of_theme(hit.theme_id)
                    if hit.kind == "theme" and ctx.alias_of_theme(hit.theme_id)
                    else hit.id[:8],
                    "title": hit.title,
                    "status": hit.status,
                    "decision": hit.decision_reason,
                    "snippet": hit.snippet[:200],
                }
                for hit in hits
            ]
        }
    )


def draft_reply(ctx: ToolContext, args: DraftReplyArgs) -> str:
    alias = args.review_id.strip().upper()
    review = ctx.reviews.get(alias)
    if review is None:
        raise ToolError(f"unknown review id {args.review_id!r}; use an id from this run (R1...).")
    signal = ctx.signals.get(review.id)
    if signal is not None and FLAG in signal.flags:
        raise ToolError(f"{alias} is quarantined (injection suspected) and gets no reply.")
    existing = ctx.store.find_proposal("reply", review_id=review.id)
    if existing is not None:
        raise ToolError(f"{alias} already has a reply proposal (status {existing.status}).")
    if ctx.replies >= ctx.max_replies:
        raise ToolError(f"reply budget for this run is used up ({ctx.max_replies}); stop drafting.")
    attempts = ctx.reply_attempts.get(alias, 0)
    report = check_reply(args.text, author=review.author, policy=ctx.policy)
    ctx.guardrail_checks.append(
        {"kind": "reply", "review": alias, "passed": report.passed, "attempt": attempts + 1}
    )
    if not report.passed:
        ctx.reply_attempts[alias] = attempts + 1
        if attempts >= 1:
            raise ToolError(
                f"draft for {alias} failed the policy twice ({report.summary()}); skip this review."
            )
        raise ToolError(
            f"draft for {alias} violates the reply policy: {report.summary()}. "
            "Fix it and call draft_reply again, or skip this review."
        )
    proposal = Proposal(
        id=str(uuid4()),
        app_id=ctx.app.id,
        run_id=ctx.run_id,
        kind="reply",
        review_id=review.id,
        theme_id=next(
            (t for t, members in ctx.theme_members.items() if review.id in members), None
        ),
        draft={"text": args.text.strip(), "review_alias": alias},
        reasoning=args.reasoning.strip(),
        guardrails=report.model_dump(),
    )
    if ctx.store.create_proposal(proposal) is None:
        raise ToolError(f"{alias} already has a reply proposal.")
    ctx.replies += 1
    ctx.created.append(proposal.id)
    return _json({"ok": True, "proposal": proposal.id[:8], "status": "proposed"})


def propose_issue(ctx: ToolContext, args: ProposeIssueArgs) -> str:
    alias = args.theme_id.strip().upper()
    theme = ctx.themes.get(alias)
    if theme is None:
        raise ToolError(
            f"unknown theme id {args.theme_id!r}; use a theme id from this run (T1...)."
        )
    if theme.kind not in ISSUE_KINDS:
        raise ToolError(f"{alias} is a {theme.kind} theme; issues are for bugs and requests.")
    existing = ctx.store.find_proposal("issue", theme_id=theme.id)
    if existing is not None:
        decision = f", decision: {existing.decision_reason}" if existing.decision_reason else ""
        raise ToolError(
            f"{alias} already has an issue proposal (status {existing.status}{decision}); "
            "do not propose it again."
        )
    if ctx.issues >= ctx.max_issues:
        raise ToolError(f"issue budget for this run is used up ({ctx.max_issues}).")
    members = set(ctx.theme_members.get(theme.id, []))
    member_reviews = {r.id: r for r in ctx.reviews.values() if r.id in members}
    evidence: list[EvidenceQuote] = []
    unknown: list[str] = []
    for item in args.evidence:
        review = ctx.reviews.get(item.review_id.strip().upper())
        if review is None or review.id not in members:
            unknown.append(item.review_id)
            continue
        evidence.append(
            EvidenceQuote(
                review_id=review.id,
                store_review_id=review.store_review_id,
                quote=item.quote.strip(),
                date=review.date.isoformat() if review.date else None,
                rating=review.rating,
                app_version=review.app_version,
            )
        )
    flags = {rid: ctx.signals[rid].flags for rid in members if rid in ctx.signals}
    member_signals = [ctx.signals[rid] for rid in members if rid in ctx.signals]
    versions = sorted({r.app_version for r in member_reviews.values() if r.app_version})
    devices = sorted(
        {d for s in member_signals for d in s.devices}
        | {o for s in member_signals for o in s.os_versions}
    )
    draft = IssueDraft(
        title=args.title.strip(),
        summary=args.summary.strip(),
        suspected_area=(args.suspected_area or "").strip() or None,
        severity=args.severity,
        evidence=evidence,
        affected_versions=versions[-6:],
        affected_devices=devices[:8],
        theme_id=theme.id,
        theme_title=theme.title,
        review_count=theme.review_count,
    )
    payload = draft.model_dump()
    report = check_issue(
        payload,
        theme_review_ids=members,
        reviews=member_reviews,
        flags=flags,
    )
    if unknown:
        report.violations.insert(
            0, Violation(rule="evidence_exists", detail=f"not reviews of {alias}: {unknown}")
        )
        report.passed = False
    ctx.guardrail_checks.append({"kind": "issue", "theme": alias, "passed": report.passed})
    if not report.passed:
        raise ToolError(f"issue for {alias} refused: {report.summary()}")
    proposal_id = str(uuid4())
    payload["body"] = render_issue_body(draft, app_name=ctx.app.name, proposal_id=proposal_id)
    proposal = Proposal(
        id=proposal_id,
        app_id=ctx.app.id,
        run_id=ctx.run_id,
        kind="issue",
        theme_id=theme.id,
        draft=payload,
        reasoning=args.reasoning.strip(),
        guardrails=report.model_dump(),
    )
    if ctx.store.create_proposal(proposal) is None:
        raise ToolError(f"{alias} already has an issue proposal.")
    ctx.issues += 1
    ctx.created.append(proposal.id)
    return _json({"ok": True, "proposal": proposal.id[:8], "status": "proposed"})


def summarise_run(ctx: ToolContext, args: SummariseRunArgs) -> str:
    ctx.summary = args.summary.strip()
    return _json({"ok": True, "next": "stop now; reply with one short sentence"})


_HINTS = frozenset(
    {"minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems", "pattern"}
)
_COMBINATORS = ("anyOf", "allOf", "oneOf")


def portable_schema(node: Any) -> Any:
    """Normalise one JSON Schema node for every provider we route to.

    Like llm_kit.schema (refs inlined first, objects closed, `title`/`default` annotations
    dropped), with two differences found in the first live run:
      - llm-kit drops *any* key named "title", including a property called `title`, while
        `required` still lists it; Gemini then answers 400 ("schema at top-level requires
        unspecified property 'title'"). Here property names are never treated as keywords.
      - numeric and length bounds are not advertised (see PortableTool).
    """
    if isinstance(node, list):
        return [portable_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _HINTS or key == "default" or (key == "title" and isinstance(value, str)):
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: portable_schema(sub) for name, sub in value.items()}
        elif key in ("items", *_COMBINATORS):
            out[key] = portable_schema(value)
        else:
            out[key] = value
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
    return out


class PortableTool(Tool):
    """A Tool whose *advertised* schema is portable: no numeric or length bounds, and
    property names never mistaken for keywords.

    Provider schema subsets disagree about bound keywords (one rejects `maxItems`, another
    ignores `minimum`), and a rejected tool schema is a 400 on every turn of the loop. The
    bounds are still enforced: llm-kit's dispatch validates every call against the full
    Pydantic model, and the descriptions state the limits in words."""

    def schema(self) -> dict[str, Any]:
        from llm_kit.schema import _inline_refs

        parameters = portable_schema(_inline_refs(self.args_model.model_json_schema()))
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


def build_tools(ctx: ToolContext) -> list[Tool]:
    Tool = PortableTool
    return [
        Tool(
            name="search_memory",
            description=(
                "Search past themes and proposals for this app (including human decisions such "
                "as 'declined: duplicate'). Call before proposing an issue."
            ),
            args_model=SearchMemoryArgs,
            func=lambda args: search_memory(ctx, args),
        ),
        Tool(
            name="draft_reply",
            description=(
                "Propose a reply to one review (R-id). Checked against the reply policy; a "
                "violation is returned so you can fix it once. Creates a proposal only."
            ),
            args_model=DraftReplyArgs,
            func=lambda args: draft_reply(ctx, args),
        ),
        Tool(
            name="propose_issue",
            description=(
                "Propose a GitHub issue for one theme (T-id) of kind bug, request or billing, "
                "with 1-6 evidence quotes copied verbatim from that theme's reviews. Creates a "
                "proposal only; a human decides."
            ),
            args_model=ProposeIssueArgs,
            func=lambda args: propose_issue(ctx, args),
        ),
        Tool(
            name="summarise_run",
            description="Record a 2-4 sentence summary of this run. Call once, at the end.",
            args_model=SummariseRunArgs,
            func=lambda args: summarise_run(ctx, args),
        ),
    ]
