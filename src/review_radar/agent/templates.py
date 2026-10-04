"""The issue template (spec section 4): title, summary, evidence quotes with review ids and
dates, suspected area, affected versions/devices, severity.

The agent supplies judgement (title, summary, suspected area, severity, which reviews are
evidence and which sentence to quote). Everything factual is filled in by code from the
store: review dates, ratings and versions come from the feed, device and OS mentions from
the extracted signals. The rendered body is what the approve endpoint sends to GitHub.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

TEMPLATE_SECTIONS = (
    "## Summary",
    "## Evidence",
    "## Suspected area",
    "## Affected versions and devices",
    "## Severity",
)


class EvidenceQuote(BaseModel):
    review_id: str
    store_review_id: str
    quote: str
    date: str | None = None
    rating: int | None = None
    app_version: str | None = None


class IssueDraft(BaseModel):
    title: str
    summary: str
    suspected_area: str | None = None
    severity: int = Field(ge=1, le=5)
    evidence: list[EvidenceQuote]
    affected_versions: list[str] = Field(default_factory=list)
    affected_devices: list[str] = Field(default_factory=list)
    theme_id: str
    theme_title: str = ""
    review_count: int = 0
    labels: list[str] = Field(default_factory=lambda: ["review-radar"])


SEVERITY_WORDS = {1: "trivial", 2: "minor", 3: "moderate", 4: "major", 5: "critical"}


def render_issue_body(draft: IssueDraft, *, app_name: str, proposal_id: str) -> str:
    lines = ["## Summary", "", draft.summary.strip(), "", "## Evidence", ""]
    for item in draft.evidence:
        facts = [f"review `{item.store_review_id}`"]
        if item.date:
            facts.append(item.date[:10])
        if item.rating:
            facts.append(f"{item.rating}★")
        if item.app_version:
            facts.append(f"v{item.app_version}")
        quote = item.quote.strip().replace("\n", " ")
        lines.append(f"> {quote}")
        lines.append(f"> — {', '.join(facts)}")
        lines.append("")
    lines += [
        "## Suspected area",
        "",
        draft.suspected_area or "Not clear from the reviews.",
        "",
        "## Affected versions and devices",
        "",
        f"- App versions (from the store): {', '.join(draft.affected_versions) or 'unknown'}",
        f"- Devices / OS mentioned: {', '.join(draft.affected_devices) or 'none mentioned'}",
        "",
        "## Severity",
        "",
        f"{draft.severity}/5 ({SEVERITY_WORDS[draft.severity]}), "
        f"{draft.review_count} review(s) in theme “{draft.theme_title or draft.title}”.",
        "",
        "---",
        f"Proposed by Review Radar from {app_name} App Store reviews and approved by an operator. "
        "Quotes are verbatim from public reviews.",
        f"<!-- review-radar:proposal:{proposal_id} -->",
    ]
    return "\n".join(lines)


def template_completeness(body: str) -> float:
    """Share of the required sections present (an eval scorer and a test helper)."""
    return sum(1 for section in TEMPLATE_SECTIONS if section in body) / len(TEMPLATE_SECTIONS)
