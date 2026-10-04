"""Export approved replies as CSV (store APIs need developer credentials, so posting replies
is out of the MVP; the operator pastes them into App Store Connect or the Play Console).

A read of stored, already-approved proposals: it changes nothing and needs no token beyond
the operator's. Columns are stable so a spreadsheet macro can rely on them.
"""

from __future__ import annotations

import csv
import io

from ..models import Proposal, Review

COLUMNS = (
    "proposal_id",
    "store_review_id",
    "review_date",
    "rating",
    "review_title",
    "review_body",
    "reply",
    "approved_at",
    "approved_by",
)


def _safe(value: object) -> str:
    """Neutralise spreadsheet formula injection: a review starting with = + - @ would be
    executed by Excel or Sheets when the CSV is opened."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def replies_csv(proposals: list[Proposal], reviews: dict[str, Review]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    for proposal in proposals:
        if proposal.kind != "reply" or proposal.status not in ("approved", "executed"):
            continue
        review = reviews.get(proposal.review_id or "")
        writer.writerow(
            [
                _safe(v)
                for v in (
                    proposal.id,
                    review.store_review_id if review else "",
                    review.date.isoformat() if review and review.date else "",
                    review.rating if review else "",
                    review.title if review else "",
                    review.body if review else "",
                    proposal.draft.get("text", ""),
                    proposal.decided_at.isoformat() if proposal.decided_at else "",
                    proposal.decided_by or "",
                )
            ]
        )
    return buffer.getvalue()
