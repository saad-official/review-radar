"""Stage 2, extract: structured signals for each review, ten reviews per model call.

Why batches of ten: one review per call would cost a full system prompt per review (the
prompt is ~600 tokens, a review ~80); fifty per call risks truncation and makes a single
off-schema item fail fifty reviews. Ten keeps the per-review overhead around 60 tokens and
a failed batch cheap to retry on the fallback route.

Everything the model returns is checked by code before it is stored:
  - the batch-local `ref` must match a review in the batch (unknown refs are dropped);
  - quotes must be verbatim substrings of the review (anything else is dropped);
  - list fields are trimmed and de-duplicated;
  - a review the model skipped gets a placeholder (`flags: extraction_missing`) instead of
    being retried forever by every future run.

Quarantined reviews (the injection detector fired) never reach a model at all: they get
rule-based signals with `model: "rules"` and the `prompt_injection` flag.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel

from ..models import Category, Review, Sentiment, Signals
from ..prompts import wrap_untrusted
from .guardrails import is_verbatim
from .injection import FLAG, detect_injection

BATCH_SIZE = 10
MAX_REVIEW_CHARS = 1500


class ReviewSignals(BaseModel):
    """One review's signals as the model must return them. Every field is required; the
    optional ones are *nullable* instead (strict structured-output modes cannot express an
    optional key, see llm_kit.schema.require_all_properties)."""

    ref: str
    category: Category
    sentiment: Sentiment
    # A plain integer, clamped to 1-5 in code: integer enums and numeric bounds are where
    # provider schema subsets disagree most (Gemini, Groq strict), so they are not sent.
    severity: int
    feature_area: str | None
    devices: list[str]
    os_versions: list[str]
    app_versions: list[str]
    quotes: list[str]


class ReviewSignalsBatch(BaseModel):
    reviews: list[ReviewSignals]


def batches(items: list[str], size: int = BATCH_SIZE) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def split_quarantined(reviews: list[Review]) -> tuple[list[Review], list[Review]]:
    clean, quarantined = [], []
    for review in reviews:
        (quarantined if detect_injection(review.text) else clean).append(review)
    return clean, quarantined


def build_message(reviews: list[Review]) -> tuple[str, dict[str, Review]]:
    refs: dict[str, Review] = {}
    parts = []
    for index, review in enumerate(reviews, start=1):
        ref = f"r{index}"
        refs[ref] = review
        attrs = {"ref": ref}
        if review.rating:
            attrs["rating"] = str(review.rating)
        text = f"{review.title}\n{review.body}" if review.title else review.body
        parts.append(wrap_untrusted("review", text[:MAX_REVIEW_CHARS], **attrs))
    header = f"Extract signals for these {len(reviews)} reviews.\n\n"
    return header + "\n\n".join(parts), refs


def _clean_list(values: list[str], limit: int = 5, length: int = 60) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        value = value.strip()[:length]
        if value and value.lower() not in seen:
            seen[value.lower()] = value
    return list(seen.values())[:limit]


def to_signals(
    batch: ReviewSignalsBatch, refs: dict[str, Review], *, model: str, prompt_version: str
) -> list[Signals]:
    signals: dict[str, Signals] = {}
    for item in batch.reviews:
        review = refs.get(item.ref.strip())
        if review is None or review.id in signals:
            continue
        quotes = [q.strip()[:240] for q in item.quotes if is_verbatim(q, review.text)][:2]
        signals[review.id] = Signals(
            review_id=review.id,
            category=item.category,
            sentiment=item.sentiment,
            severity=min(5, max(1, int(item.severity))),
            feature_area=(item.feature_area or "").strip().lower()[:60] or None,
            devices=_clean_list(item.devices),
            os_versions=_clean_list(item.os_versions),
            app_versions=_clean_list(item.app_versions),
            quotes=quotes,
            model=model,
            prompt_version=prompt_version,
        )
    for review in refs.values():
        if review.id not in signals:
            signals[review.id] = placeholder(review, "extraction_missing", model, prompt_version)
    return list(signals.values())


def placeholder(review: Review, flag: str, model: str, prompt_version: str) -> Signals:
    sentiment = "negative" if (review.rating or 3) <= 2 else "positive"
    if review.rating == 3:
        sentiment = "neutral"
    return Signals(
        review_id=review.id,
        category="other",
        sentiment=sentiment,  # type: ignore[arg-type]
        severity=1,
        flags=[flag],
        model=model,
        prompt_version=prompt_version,
    )


def quarantine_signals(review: Review) -> Signals:
    return placeholder(review, FLAG, "rules", "injection.v1")
