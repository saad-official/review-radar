from datetime import timedelta

from review_radar.llm import LLMRouteError
from review_radar.models import utcnow
from review_radar.service import RunService, create_app

from .conftest import make_engine


def new_run(engine):
    app = create_app(engine, store_kind="ios", store_id="1", name="Demo", github_repo="a/b")
    service = RunService(engine)
    return app, service, service.create_run(app.id)


def test_every_tool_call_has_a_result_in_order(settings):
    engine = make_engine(settings)
    _, service, run = new_run(engine)
    service.process(run.id)
    steps = engine.store.list_steps(run.id)
    propose = [s for s in steps if s.stage == "propose" and s.kind in ("tool_call", "tool_result")]
    calls = [s for s in propose if s.kind == "tool_call"]
    results = [s for s in propose if s.kind == "tool_result"]
    assert len(calls) == len(results) > 0
    for call, result in zip(calls, results, strict=True):
        assert result.seq == call.seq + 1 and result.name == call.name
    models = [s for s in steps if s.kind == "model"]
    assert all(s.usage and s.usage["calls"] == 1 for s in models)
    extract_results = [s for s in steps if s.name == "extract_signals" and s.kind == "tool_result"]
    assert all(s.usage["prompt_tokens"] > 0 for s in extract_results)
    assert steps[-1].result["status"] == "done"


def test_run_resumes_from_checkpoints(settings):
    holder = {}
    engine = make_engine(settings, holder=holder)
    _, service, run = new_run(engine)
    first = service.process(run.id, time_budget_s=30)  # too short to start the propose loop
    assert first.resumable and first.status == "queued"
    paused = engine.store.get_run(run.id)
    assert paused.status == "clustering" and paused.lease_until is None and paused.attempts == 0
    calls_before = paused.usage["calls"]
    assert calls_before == 3  # two extract batches + theme naming

    second = service.process(run.id, time_budget_s=240)
    assert second.status == "done"
    assert holder["extract"].calls == []  # extraction was not repeated
    done = engine.store.get_run(run.id)
    assert done.usage["calls"] > calls_before  # the ledger carried over
    assert any(s.name == "suspended" for s in engine.store.list_steps(run.id))


def test_lease_blocks_a_second_processor(settings):
    engine = make_engine(settings)
    _, service, run = new_run(engine)
    assert engine.store.claim_run(run.id, lease_s=300, max_attempts=4) is not None
    assert service.process(run.id).status == "in_progress"
    engine.store.update_run(run.id, lease_until=utcnow() - timedelta(seconds=1))
    assert service.process(run.id).status == "done"
    assert service.process(run.id).message == "already finished"


def test_budget_stops_the_run_cleanly(settings):
    tight = settings.model_copy(update={"max_usd_per_run": 0.0002})
    engine = make_engine(tight)
    app, service, run = new_run(engine)
    assert service.process(run.id).status == "done"
    finished = engine.store.get_run(run.id)
    assert finished.stats["budget_hit"] is True
    assert finished.usage["usd"] <= 0.0002 + 0.0002  # at most one call past the check
    assert engine.store.list_proposals(app.id) == []
    assert any(s.name == "budget" for s in engine.store.list_steps(run.id))


def test_iteration_cap(settings):
    def chatty(ctx):
        return [[("search_memory", {"query": f"query number {i}"})] for i in range(40)]

    engine = make_engine(settings, plan=chatty)
    _, service, run = new_run(engine)
    service.process(run.id)
    finished = engine.store.get_run(run.id)
    assert finished.stats["stop_reason"] == "max_iterations"
    assert finished.stats["iterations"] == 24
    assert finished.status == "done"


def test_quota_exhaustion_fails_with_a_clear_code(settings):
    error = LLMRouteError(
        "extract",
        [
            "groq:openai/gpt-oss-20b: daily quota exhausted for 3000s more",
            "gemini:gemini-3.5-flash-lite: LLMTransientError: 429 RESOURCE_EXHAUSTED",
        ],
    )
    engine = make_engine(settings, structured_script={"extract": error})
    _, service, run = new_run(engine)
    outcome = service.process(run.id)
    assert outcome.status == "failed" and outcome.message.startswith("model_quota_exhausted")
    failed = engine.store.get_run(run.id)
    assert failed.error.startswith("model_quota_exhausted") and failed.finished_at
    # The fetched reviews are kept; a later run picks them up (signals are the checkpoint).
    assert len(engine.store.unprocessed_review_ids(failed.app_id, 100)) == 11


def test_feed_errors_are_not_fatal(settings):
    from review_radar.ingest.base import IngestError

    from .conftest import FakeSource

    engine = make_engine(settings, source=FakeSource(error=IngestError("feed_error", "503")))
    _, service, run = new_run(engine)
    assert service.process(run.id).status == "done"
    fetch = next(s for s in engine.store.list_steps(run.id) if s.kind == "tool_result")
    assert fetch.result["error"] == "feed_error"


def test_embedding_quota_fails_cleanly_and_next_run_catches_up(settings):
    """Regression (live smoke): a 429 from the embeddings API surfaced as an unclassified
    error and left analysed reviews that no later run would ever embed."""
    from llm_kit import LLMTransientError

    from review_radar.embeddings import HashEmbedder

    class QuotaEmbedder(HashEmbedder):
        def embed(self, texts, *, label="embed"):
            raise LLMTransientError('429 from embeddings: {"status": "RESOURCE_EXHAUSTED"}')

    engine = make_engine(settings)
    engine.embedder_builder = lambda ledger: QuotaEmbedder(ledger)
    app, service, run = new_run(engine)
    outcome = service.process(run.id)
    assert outcome.status == "failed" and outcome.message.startswith("model_quota_exhausted")
    assert len(engine.store.signals) == 12  # extraction work is kept

    engine.embedder_builder = lambda ledger: HashEmbedder(ledger)
    run2 = service.create_run(app.id)
    assert service.process(run2.id).status == "done"
    stats = engine.store.get_run(run2.id).stats
    assert stats["working_set"] == 11 and stats["themes_new"] >= 2  # quarantined one excluded
