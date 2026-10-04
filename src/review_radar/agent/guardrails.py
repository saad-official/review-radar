"""Deterministic guardrails: checked in code before a draft can enter the approval queue.

The prompt states the reply policy too (cheap, and it lowers the refusal rate), but the
guarantee comes from here, for the same reason Changelog Forge verifies references in code:
a prompt is a request the model can ignore; a regex is not.

Reply rules: length (350 for the App Store, lower if the app policy says so), no URLs
unless the policy allows them (optionally only listed domains), no refund/compensation
promises, no dates or timelines ("we will fix this by..."), no unreleased features, no
personal data (emails, phone numbers, the reviewer's own name), plus the app's own banned
phrases. Issue rules: at least one evidence review that exists in this theme and is not
quarantined, at most 6 quotes, every quote verbatim from its review, a sane title.

App policy directives (one per line in `apps.policy`; everything else is free text that
goes to the prompt):

    allow_urls: true
    allowed_domains: support.example.com, example.com
    banned: lifetime deal; beta
    max_reply_chars: 300
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .injection import FLAG, detect_injection

MAX_REPLY_CHARS = 350
MAX_ISSUE_QUOTES = 6


class Violation(BaseModel):
    rule: str
    detail: str


class GuardrailReport(BaseModel):
    passed: bool
    violations: list[Violation] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        return "; ".join(f"{v.rule}: {v.detail}" for v in self.violations) or "all checks passed"


class Policy(BaseModel):
    text: str = ""
    allow_urls: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    banned: list[str] = Field(default_factory=list)
    max_reply_chars: int = MAX_REPLY_CHARS

    @classmethod
    def parse(cls, text: str | None) -> Policy:
        policy = cls(text=(text or "").strip())
        for line in policy.text.splitlines():
            key, sep, value = line.partition(":")
            if not sep:
                continue
            key = key.strip().lower().replace(" ", "_").replace("-", "_")
            value = value.strip()
            if key == "allow_urls":
                policy.allow_urls = value.lower() in ("true", "yes", "1", "on")
            elif key == "allowed_domains":
                policy.allowed_domains = [
                    d.strip().lower() for d in re.split(r"[,\s]+", value) if d.strip()
                ]
                policy.allow_urls = policy.allow_urls or bool(policy.allowed_domains)
            elif key in ("banned", "banned_phrases"):
                policy.banned = [p.strip().lower() for p in re.split(r"[;,]", value) if p.strip()]
            elif key == "max_reply_chars" and value.isdigit():
                # The policy can tighten the store's limit, never loosen it.
                policy.max_reply_chars = max(40, min(MAX_REPLY_CHARS, int(value)))
        return policy


_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"

BANNED: dict[str, list[str]] = {
    "refund_promise": [
        r"\brefund",
        r"\bmoney\s+back\b",
        r"\breimburs",
        r"\bcompensat",
        r"\bfree\s+(?:month|months|trial|premium|subscription)\b",
        r"\b(?:credit|credited)\s+(?:your|you|to)\b",
    ],
    "date_or_timeline": [
        rf"\b(?:by|before|until|within|on)\s+(?:{_WEEKDAYS}|tomorrow|tonight|the\s+end\s+of|next\s+\w+|\d)",
        r"\bnext\s+(?:week|month|update|release|version|build)\b",
        r"\btomorrow\b",
        rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}\b",
        r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
        r"\b(?:in|within)\s+(?:a|the)\s+(?:(?:next|coming)\s+)?(?:few\s+|couple\s+of\s+)?"
        r"(?:days|weeks|months)\b",
        r"\bwe\s*(?:will|'ll|’ll)\s+(?:have\s+(?:it|this)\s+)?fix(?:ed)?\b",
        r"\bwill\s+be\s+fixed\b",
        r"\bguarantee",
    ],
    "unreleased_feature": [
        r"\bcoming\s+soon\b",
        r"\bupcoming\s+(?:feature|release|update|version|change)",
        r"\b(?:next|future|new|upcoming)\s+(?:version|release|update)\s+will\b",
        r"\bwe\s*(?:are|'re|’re)\s+(?:working\s+on|building|launching|adding)\s+(?:a|an)\s+new\b",
        r"\bsoon\s+(?:be\s+able|you\s+will|you'll)\b",
        r"\b(?:beta|unreleased|secret)\s+feature",
    ],
}
_BANNED = {
    rule: re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)
    for rule, patterns in BANNED.items()
}
_URL = re.compile(
    r"(?:https?://|www\.)\S+|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|app|co|me|ly|gg|dev)\b(?:/\S*)?",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")


def _domain(url: str) -> str:
    host = re.sub(r"^(?:https?://)?(?:www\.)?", "", url.lower()).split("/")[0]
    return host.rstrip(".,;:!?)")


def check_reply(text: str, *, author: str | None, policy: Policy) -> GuardrailReport:
    violations: list[Violation] = []
    checks = ["length", "urls", *BANNED, "personal_data", "app_banned_phrases", "instructions"]
    stripped = text.strip()
    if not stripped:
        violations.append(Violation(rule="length", detail="reply is empty"))
    if len(stripped) > policy.max_reply_chars:
        violations.append(
            Violation(
                rule="length",
                detail=f"{len(stripped)} characters; the limit is {policy.max_reply_chars}",
            )
        )
    for match in _URL.finditer(stripped):
        url = match.group(0)
        if not policy.allow_urls:
            violations.append(Violation(rule="urls", detail=f"links are not allowed: {url!r}"))
        elif policy.allowed_domains and not any(
            _domain(url) == d or _domain(url).endswith("." + d) for d in policy.allowed_domains
        ):
            violations.append(
                Violation(rule="urls", detail=f"{_domain(url)!r} is not an allowed domain")
            )
    for rule, pattern in _BANNED.items():
        match = pattern.search(stripped)
        if match:
            violations.append(Violation(rule=rule, detail=f"banned phrase {match.group(0)!r}"))
    for pattern, what in ((_EMAIL, "an email address"), (_PHONE, "a phone number")):
        if pattern.search(stripped):
            violations.append(Violation(rule="personal_data", detail=f"contains {what}"))
    if author:
        name = author.strip()
        if len(name) >= 3 and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", stripped, re.I):
            violations.append(Violation(rule="personal_data", detail="repeats the reviewer's name"))
    lowered = stripped.lower()
    for phrase in policy.banned:
        if phrase and phrase in lowered:
            violations.append(
                Violation(rule="app_banned_phrases", detail=f"the app policy bans {phrase!r}")
            )
    if detect_injection(stripped):
        violations.append(Violation(rule="instructions", detail="contains instruction-like text"))
    return GuardrailReport(passed=not violations, violations=violations, checks=checks)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'").replace("‘", "'")).strip().lower()


def is_verbatim(quote: str, source: str) -> bool:
    q = normalise(quote).strip(" .,!?\"'…")
    return len(q) >= 3 and q in normalise(source)


def check_issue(
    draft: dict[str, Any],
    *,
    theme_review_ids: set[str],
    reviews: dict[str, Any],
    flags: dict[str, list[str]],
) -> GuardrailReport:
    """`reviews` maps review id -> Review (or anything with `.text`); `flags` review id ->
    deterministic flags. Evidence must be members of this theme, exist, and be clean."""
    violations: list[Violation] = []
    checks = ["title", "summary", "evidence_exists", "evidence_clean", "quotes", "instructions"]
    title = str(draft.get("title") or "").strip()
    summary = str(draft.get("summary") or "").strip()
    if not 8 <= len(title) <= 120:
        violations.append(Violation(rule="title", detail="title must be 8-120 characters"))
    if not summary:
        violations.append(Violation(rule="summary", detail="summary is empty"))
    evidence = draft.get("evidence") or []
    valid = [
        e
        for e in evidence
        if e.get("review_id") in theme_review_ids and e.get("review_id") in reviews
    ]
    if not valid:
        violations.append(
            Violation(
                rule="evidence_exists",
                detail="at least one evidence review id must exist in this theme",
            )
        )
    tainted = [e["review_id"] for e in valid if FLAG in flags.get(e["review_id"], [])]
    if tainted:
        violations.append(
            Violation(
                rule="evidence_clean",
                detail=f"evidence includes quarantined reviews (prompt injection): {tainted}",
            )
        )
    quotes = [e for e in evidence if e.get("quote")]
    if len(quotes) > MAX_ISSUE_QUOTES:
        violations.append(
            Violation(rule="quotes", detail=f"{len(quotes)} quotes; at most {MAX_ISSUE_QUOTES}")
        )
    for item in quotes:
        review = reviews.get(item.get("review_id", ""))
        if review is None or not is_verbatim(item["quote"], review.text):
            violations.append(
                Violation(
                    rule="quotes",
                    detail=f"quote for {item.get('review_id')} is not verbatim from that review",
                )
            )
    if detect_injection(f"{title}\n{summary}"):
        violations.append(Violation(rule="instructions", detail="contains instruction-like text"))
    return GuardrailReport(passed=not violations, violations=violations, checks=checks)
