"""The proposal view's shape: what web/lib/api.ts reads (draft.body, draft.evidence,
guardrails.checks as objects) on top of the stored fields, which must not change."""

from datetime import UTC, datetime

import pytest

from review_radar.api.schemas import ProposalView, ReviewBrief, guardrail_checks

from .conftest import make_engine
from .test_api import OP, client_for, make_app

NOW = datetime(2026, 10, 5, 7, 0, tzinfo=UTC)


def view(**overrides) -> ProposalView:
    data = {
        "id": "p1",
        "app_id": "a1",
        "run_id": "run1",
        "kind": "reply",
        "status": "proposed",
        "review_id": "rev-1",
        "theme_id": None,
        "draft": {"text": "Sorry about that. Please contact support.", "review_alias": "R1"},
        "reasoning": "a complaint",
        "guardrails": {"passed": True, "violations": [], "checks": ["length", "urls"]},
        "decided_by": None,
        "decided_at": None,
        "decision_reason": None,
        "reason": None,
        "result": None,
        "review": ReviewBrief(
            id="rev-1",
            store_review_id="11843097337",
            rating=1,
            title="I’m angry",
            body="It crashes on launch.",
            app_version="9.1.88",
            date=NOW,
        ),
        "theme": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return ProposalView(**data)


def test_reply_view_adds_body_evidence_and_check_objects():
    v = view().model_dump(mode="json")
    draft = v["draft"]
    assert draft["text"] == draft["body"] == "Sorry about that. Please contact support."
    assert draft["review_alias"] == "R1"
    assert draft["evidence"] == [
        {
            "review_id": "rev-1",
            "store_review_id": "11843097337",
            "quote": "It crashes on launch.",
            "date": "2026-10-05T07:00:00+00:00",
            "rating": 1,
            "app_version": "9.1.88",
        }
    ]
    assert v["guardrails"] == {
        "passed": True,
        "violations": [],
        "checks": [
            {"name": "length", "ok": True, "note": None},
            {"name": "urls", "ok": True, "note": None},
        ],
    }
    assert v["review_id"] == "rev-1"


def test_reply_evidence_quote_is_cut_near_240_chars():
    body = "word " * 100
    review = view().review.model_copy(update={"body": body})
    quote = view(review=review).draft["evidence"][0]["quote"]
    assert quote.endswith("…") and len(quote) <= 241
    assert body.startswith(quote[:-1])


def test_reply_without_review_or_with_evidence():
    assert "evidence" not in view(review=None).draft
    kept = [{"review_id": "x", "quote": "already there", "date": None}]
    draft = {"text": "Thanks.", "evidence": kept}
    assert view(draft=draft).draft["evidence"] == kept


def test_issue_view_keeps_its_evidence_and_body():
    evidence = [{"review_id": "rev-1", "quote": "crashes on launch", "date": "2026-10-05"}]
    draft = {"title": "Crash", "summary": "s", "evidence": evidence, "body": "## Crash\n..."}
    v = view(kind="issue", review_id=None, review=None, draft=draft)
    assert v.draft["evidence"] == evidence and v.draft["body"] == "## Crash\n..."
    assert "text" not in v.draft


def test_guardrail_checks_mark_violations():
    checks = guardrail_checks(
        {
            "passed": False,
            "checks": ["length", "urls", "refund_promise"],
            "violations": [
                {"rule": "urls", "detail": "links are not allowed: example.com"},
                {"rule": "urls", "detail": "second link"},
                {"rule": "evidence_exists", "detail": "not reviews of T1: ['R9']"},
            ],
        }
    )
    assert checks == [
        {"name": "length", "ok": True, "note": None},
        {"name": "urls", "ok": False, "note": "links are not allowed: example.com; second link"},
        {"name": "refund_promise", "ok": True, "note": None},
        {"name": "evidence_exists", "ok": False, "note": "not reviews of T1: ['R9']"},
    ]
    g = view(guardrails={"passed": False, "checks": ["urls"], "violations": [{"rule": "urls"}]})
    assert g.guardrails["passed"] is False and g.guardrails["checks"][0]["ok"] is False


def test_guardrail_checks_already_shaped_and_empty():
    shaped = [{"name": "length", "ok": False, "note": "too long"}]
    assert guardrail_checks({"checks": shaped}) == shaped
    empty = view(guardrails={}).guardrails
    assert empty == {"checks": [], "violations": [], "passed": True}


@pytest.mark.anyio
async def test_proposal_routes_return_the_web_shape(settings):
    engine = make_engine(settings)
    async with client_for(engine) as client:
        app = await make_app(client, public=True)
        await client.post(f"/api/apps/{app['id']}/runs", json={}, headers=OP)
        response = await client.get(f"/api/apps/{app['id']}/proposals")
        assert response.headers["content-type"] == "application/json; charset=utf-8"
        proposals = response.json()

    reply = next(p for p in proposals if p["kind"] == "reply")
    assert reply["draft"]["body"] == reply["draft"]["text"] and reply["draft"]["review_alias"]
    [evidence] = reply["draft"]["evidence"]
    assert evidence["review_id"] == reply["review"]["id"] == reply["review_id"]
    assert evidence["store_review_id"] == reply["review"]["store_review_id"]
    assert evidence["quote"] and reply["review"]["body"].startswith(evidence["quote"].rstrip("…"))
    assert evidence["date"] is not None
    checks = reply["guardrails"]["checks"]
    assert {"length", "urls", "personal_data"} <= {c["name"] for c in checks}
    assert all(set(c) == {"name", "ok", "note"} and c["ok"] for c in checks)
    assert reply["guardrails"]["passed"] is True and reply["guardrails"]["violations"] == []

    issue = next(p for p in proposals if p["kind"] == "issue")
    assert issue["draft"]["evidence"] and issue["draft"]["evidence"][0]["quote"]
    assert (
        "Evidence" in issue["draft"]["body"]
        or issue["draft"]["evidence"][0]["quote"] in (issue["draft"]["body"])
    )
    assert "text" not in issue["draft"] and issue["theme_id"]
    assert all(isinstance(c, dict) and c["ok"] for c in issue["guardrails"]["checks"])
