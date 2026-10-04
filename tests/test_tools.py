import json
from datetime import UTC, datetime

from llm_kit import dispatch

from review_radar.agent.tools import ToolContext, build_tools
from review_radar.db import MemoryStore
from review_radar.models import App, Review, Signals, Theme


def context(**overrides) -> ToolContext:
    store = MemoryStore()
    app = App(id="app", store="ios", store_id="1", name="Demo")
    store.create_app(app)
    r1 = Review(
        id="rev1",
        app_id="app",
        store_review_id="s1",
        author="Jordan",
        rating=1,
        body="It crashes when I open the library.",
        app_version="9.1",
        date=datetime(2026, 10, 1, tzinfo=UTC),
    )
    r2 = Review(id="rev2", app_id="app", store_review_id="s2", rating=5, body="Lovely")
    theme = Theme(
        id="th1", app_id="app", title="Library crash", summary="s", kind="bug", review_count=1
    )
    praise = Theme(id="th2", app_id="app", title="Love", summary="s", kind="praise", review_count=1)
    sig = Signals(
        review_id="rev1",
        category="bug",
        sentiment="negative",
        severity=4,
        devices=["iPhone 15"],
        model="m",
        prompt_version="p",
    )
    ctx = ToolContext(
        store=store,
        app=app,
        run_id="run",
        reviews={"R1": r1, "R2": r2},
        signals={"rev1": sig},
        themes={"T1": theme, "T2": praise},
        theme_members={"th1": ["rev1"], "th2": ["rev2"]},
        **overrides,
    )
    return ctx


def call(ctx, name, args):
    registry = {tool.name: tool for tool in build_tools(ctx)}
    outcome = dispatch(registry, "c1", name, json.dumps(args))
    return outcome, json.loads(outcome.content)


ISSUE = {
    "theme_id": "T1",
    "title": "Library crashes on open",
    "summary": "Opening the library crashes the app.",
    "suspected_area": "library",
    "severity": 4,
    "evidence": [{"review_id": "R1", "quote": "crashes when I open the library"}],
    "reasoning": "Severe crash.",
}


def test_tool_schemas_are_closed_objects():
    for tool in build_tools(context()):
        schema = tool.schema()["function"]["parameters"]
        assert schema["additionalProperties"] is False
        # Bounds are not advertised (portable across providers) but still enforced.
        assert "maxLength" not in json.dumps(schema) and "maximum" not in json.dumps(schema)


def test_argument_validation_rejects_out_of_range_and_unknown_tools():
    ctx = context()
    outcome, body = call(ctx, "propose_issue", {**ISSUE, "severity": 9})
    assert not outcome.ok and body["error"] == "invalid_arguments"
    outcome, body = call(ctx, "propose_issue", {**ISSUE, "evidence": ISSUE["evidence"] * 7})
    assert body["error"] == "invalid_arguments"
    outcome, body = call(ctx, "draft_reply", {"review_id": "R1"})
    assert body["error"] == "invalid_arguments"
    outcome, body = call(ctx, "create_github_issue", {"title": "x"})
    assert body["error"] == "unknown_tool"
    assert ctx.store.proposals == {}


def test_propose_issue_builds_facts_from_the_store():
    ctx = context()
    outcome, body = call(ctx, "propose_issue", ISSUE)
    assert outcome.ok and body["status"] == "proposed"
    proposal = next(iter(ctx.store.proposals.values()))
    draft = proposal.draft
    assert draft["evidence"][0]["store_review_id"] == "s1"
    assert draft["evidence"][0]["date"].startswith("2026-10-01")
    assert draft["affected_versions"] == ["9.1"] and draft["affected_devices"] == ["iPhone 15"]
    assert proposal.status == "proposed" and proposal.guardrails["passed"]
    # Second proposal for the same theme is refused (memory as a constraint).
    outcome, body = call(ctx, "propose_issue", ISSUE)
    assert body["error"] == "tool_failed" and "already has an issue proposal" in body["message"]


def test_propose_issue_refusals():
    ctx = context()
    _, body = call(ctx, "propose_issue", {**ISSUE, "theme_id": "T2"})
    assert "praise theme" in body["message"]
    _, body = call(ctx, "propose_issue", {**ISSUE, "theme_id": "T9"})
    assert "unknown theme" in body["message"]
    bad = {**ISSUE, "evidence": [{"review_id": "R2", "quote": "Lovely"}]}
    _, body = call(ctx, "propose_issue", bad)
    assert "not reviews of T1" in body["message"]
    fake_quote = {**ISSUE, "evidence": [{"review_id": "R1", "quote": "deletes my playlists"}]}
    _, body = call(ctx, "propose_issue", fake_quote)
    assert "not verbatim" in body["message"]
    assert ctx.store.proposals == {}


def test_draft_reply_guardrail_feedback_then_skip():
    ctx = context()
    _, body = call(
        ctx, "draft_reply", {"review_id": "R1", "text": "We will refund you.", "reasoning": "r"}
    )
    assert "violates the reply policy" in body["message"] and "refund" in body["message"]
    _, body = call(
        ctx, "draft_reply", {"review_id": "r1", "text": "Fixed by Monday!", "reasoning": "r"}
    )
    assert "failed the policy twice" in body["message"]
    assert ctx.guardrail_checks == [
        {"kind": "reply", "review": "R1", "passed": False, "attempt": 1},
        {"kind": "reply", "review": "R1", "passed": False, "attempt": 2},
    ]
    assert ctx.store.proposals == {}


def test_draft_reply_success_duplicate_and_budget():
    ctx = context(max_replies=1)
    text = "Sorry about the crash. Please contact support from the app settings."
    outcome, _ = call(ctx, "draft_reply", {"review_id": "R1", "text": text, "reasoning": "r"})
    assert outcome.ok
    proposal = next(iter(ctx.store.proposals.values()))
    assert proposal.review_id == "rev1" and proposal.theme_id == "th1"
    _, body = call(ctx, "draft_reply", {"review_id": "R1", "text": text, "reasoning": "r"})
    assert "already has a reply" in body["message"]
    _, body = call(ctx, "draft_reply", {"review_id": "R2", "text": text, "reasoning": "r"})
    assert "budget" in body["message"]


def test_quarantined_review_gets_no_reply():
    ctx = context()
    ctx.signals["rev2"] = Signals(
        review_id="rev2",
        category="other",
        sentiment="neutral",
        severity=1,
        flags=["prompt_injection"],
        model="rules",
        prompt_version="injection.v1",
    )
    _, body = call(ctx, "draft_reply", {"review_id": "R2", "text": "Hi", "reasoning": "r"})
    assert "quarantined" in body["message"]


def test_search_memory_and_summary():
    ctx = context()
    call(ctx, "propose_issue", ISSUE)
    outcome, body = call(ctx, "search_memory", {"query": "library crash"})
    assert outcome.ok and body["results"][0]["kind"] in ("theme", "proposal")
    outcome, _ = call(ctx, "summarise_run", {"summary": "One crash theme proposed."})
    assert outcome.ok and ctx.summary == "One crash theme proposed."


def test_property_named_title_survives_schema_normalisation():
    """Regression (first live run): llm-kit's normaliser deleted the `title` property while
    `required` still listed it, and Gemini rejected the propose_issue schema with a 400."""
    tools = {t.name: t.schema()["function"]["parameters"] for t in build_tools(context())}
    issue = tools["propose_issue"]
    assert "title" in issue["properties"] and set(issue["required"]) <= set(issue["properties"])
    for schema in tools.values():
        assert set(schema.get("required", [])) <= set(schema["properties"])


def test_structured_output_schemas_have_no_title_property():
    from llm_kit.schema import require_all_properties, to_provider_schema

    from review_radar.agent.cluster import ThemeNameBatch
    from review_radar.agent.extract import ReviewSignalsBatch

    for model in (ThemeNameBatch, ReviewSignalsBatch):
        text = json.dumps(require_all_properties(to_provider_schema(model)))
        schema = json.loads(text)
        item = next(iter(schema["properties"].values()))["items"]
        assert set(item["required"]) == set(item["properties"])
