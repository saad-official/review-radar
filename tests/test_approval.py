import pytest
from pydantic import SecretStr

from review_radar.crypto import decrypt_secret
from review_radar.executors.github import GitHubIssueError
from review_radar.service import ProposalService, RunService, ServiceError, create_app

from .conftest import ENCRYPTION_KEY, FakeGitHub, make_engine


def setup(settings, **app_kwargs):
    engine = make_engine(settings)
    kwargs = {"github_repo": "acme/issues", **app_kwargs}
    app = create_app(engine, store_kind="ios", store_id="1", name="Demo", **kwargs)
    service = RunService(engine)
    run = service.create_run(app.id)
    assert service.process(run.id).status == "done"
    issue = engine.store.list_proposals(app.id, kind="issue")[0]
    reply = engine.store.list_proposals(app.id, kind="reply")[0]
    return engine, app, issue, reply


def test_approve_issue_executes_exactly_once(settings):
    engine, app, issue, _ = setup(settings, github_token="github_pat_app_specific_token_1234")
    gate = ProposalService(engine)
    first = gate.approve(issue.id)
    assert first.status == "executed"
    assert first.result["url"] == "https://github.com/acme/issues/issues/1"
    again = gate.approve(issue.id)
    assert again.status == "executed" and len(FakeGitHub.created) == 1
    created = FakeGitHub.created[0]
    assert created["title"] == issue.draft["title"] and "## Evidence" in created["body"]
    # The per-app token (stored encrypted) was used, not the env fallback.
    assert created["token"] == "github_pat_app_specific_token_1234"
    stored = engine.store.get_app(app.id).github_token_ciphertext
    assert stored.startswith("v1.") and "github_pat" not in stored
    assert decrypt_secret(ENCRYPTION_KEY, app.id, stored) == created["token"]
    types = [e.type for e in engine.store.list_events(app.id)]
    assert "proposal.approved" in types and "issue.created" in types


def test_env_token_fallback_and_missing_token(settings):
    engine, _, issue, _ = setup(settings)
    gate = ProposalService(engine)
    with pytest.raises(ServiceError) as exc:
        gate.approve(issue.id)
    assert exc.value.code == "no_github_token"
    assert engine.store.get_proposal(issue.id).status == "proposed"  # unchanged
    engine.settings = settings.model_copy(update={"github_token": SecretStr("env-token")})
    assert gate.approve(issue.id).status == "executed"
    assert FakeGitHub.created[0]["token"] == "env-token"


def test_no_repo_refused_before_state_change(settings):
    engine, _, issue, _ = setup(settings, github_repo=None)
    with pytest.raises(ServiceError) as exc:
        ProposalService(engine).approve(issue.id)
    assert exc.value.code == "no_github_repo"
    assert engine.store.get_proposal(issue.id).status == "proposed"


def test_github_failure_marks_failed_and_retry_executes(settings):
    engine, _, issue, _ = setup(settings, github_token="tok-12345678")
    gate = ProposalService(engine)
    FakeGitHub.fail_with = GitHubIssueError("github_forbidden", "nope")
    failed = gate.approve(issue.id)
    assert failed.status == "failed" and failed.result["error"] == "github_forbidden"
    FakeGitHub.fail_with = None
    assert gate.approve(issue.id).status == "executed"
    assert len(FakeGitHub.created) == 1


def test_reply_approve_is_idempotent_and_exported(settings):
    engine, _, _, reply = setup(settings)
    gate = ProposalService(engine)
    approved = gate.approve(reply.id)
    assert approved.status == "approved" and approved.decided_by == "operator"
    assert gate.approve(reply.id).decided_at == approved.decided_at
    assert FakeGitHub.created == []  # replies never call an executor


def test_edits_are_rechecked_by_guardrails(settings):
    engine, _, issue, reply = setup(settings, github_token="tok-12345678")
    gate = ProposalService(engine)
    with pytest.raises(ServiceError) as exc:
        gate.approve(reply.id, edits={"text": "We will refund you by Friday."})
    assert exc.value.code == "guardrail_failed"
    assert engine.store.get_proposal(reply.id).status == "proposed"
    edited = gate.approve(reply.id, edits={"text": "Thanks, we are looking into it."})
    assert edited.draft["text"] == "Thanks, we are looking into it." and edited.draft["edited"]
    with pytest.raises(ServiceError):
        gate.approve(issue.id, edits={"evidence": []})
    done = gate.approve(issue.id, edits={"title": "Crash on launch since 9.1.88"})
    assert done.status == "executed"
    assert FakeGitHub.created[0]["title"] == "Crash on launch since 9.1.88"


def test_reject_stores_reason_in_memory(settings):
    engine, app, issue, _ = setup(settings)
    gate = ProposalService(engine)
    rejected = gate.reject(issue.id, reason="duplicate of #42")
    assert rejected.status == "rejected" and rejected.decision_reason == "duplicate of #42"
    assert gate.reject(issue.id, reason="again").decision_reason == "duplicate of #42"
    with pytest.raises(ServiceError) as exc:
        gate.approve(issue.id)
    assert exc.value.code == "proposal_rejected"
    hits = engine.store.search_memory(app.id, "duplicate crash launch", None)
    assert any(h.kind == "proposal" and h.decision_reason == "duplicate of #42" for h in hits)


def test_cannot_reject_after_execution(settings):
    engine, _, issue, _ = setup(settings, github_token="tok-12345678")
    gate = ProposalService(engine)
    gate.approve(issue.id)
    with pytest.raises(ServiceError) as exc:
        gate.reject(issue.id, reason="too late")
    assert exc.value.status == 409


def test_rejected_theme_is_never_reproposed(settings):
    engine, app, issue, _ = setup(settings)
    ProposalService(engine).reject(issue.id, reason="won't fix")
    # A later run that links new reviews to the same theme: the agent tries again.
    source_reviews = engine.source_builder(app).reviews
    source_reviews.append(
        source_reviews[0].model_copy(
            update={"store_review_id": "s200", "body": "The app crashes on launch after update."}
        )
    )
    service = RunService(engine)
    run = service.create_run(app.id)
    assert service.process(run.id).status == "done"
    issues = engine.store.list_proposals(app.id, kind="issue")
    assert len(issues) == 1 and issues[0].status == "rejected"
    refusals = [
        s
        for s in engine.store.list_steps(run.id)
        if s.kind == "tool_result" and s.name == "propose_issue"
    ]
    assert refusals and "won't fix" in refusals[0].result["content"]


def test_unknown_proposal(settings):
    engine = make_engine(settings)
    with pytest.raises(ServiceError) as exc:
        ProposalService(engine).approve("missing")
    assert exc.value.status == 404
