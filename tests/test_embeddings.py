"""Embedding providers: Voyage over a mocked HTTP API (no real calls), the factory, and the
dimension plumbing from EMBEDDING_DIMENSIONS down to the request body and the vectors."""

import json
import math

import httpx
import pytest
import respx
from llm_kit import Ledger, LLMPermanentError, LLMTransientError
from pydantic import ValidationError

from review_radar.config import AppSettings
from review_radar.embeddings import (
    GeminiEmbedder,
    HashEmbedder,
    VoyageEmbedder,
    make_embedder,
)
from review_radar.engine import Engine
from review_radar.service import RunService, create_app

from .conftest import make_engine

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


def voyage_reply(request: httpx.Request, *, dims: int | None = None) -> httpx.Response:
    """A well-formed Voyage response: unnormalised vectors (norm 2) in reverse index order,
    so the client has to sort by `index` and normalise."""
    body = json.loads(request.content)
    size = dims or body["output_dimension"]
    data = [
        {"object": "embedding", "index": i, "embedding": [2.0 / math.sqrt(size)] * size}
        for i in range(len(body["input"]))
    ]
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": list(reversed(data)),
            "model": body["model"],
            "usage": {"total_tokens": 7 * len(body["input"])},
        },
    )


def settings_for(**overrides) -> AppSettings:
    values = {
        "_env_file": None,
        "database_url": None,
        "gemini_api_key": None,
        "voyage_api_key": None,
    }
    values.update(overrides)
    return AppSettings(**values)


# ------------------------------------------------------------------ Voyage


@respx.mock
def test_voyage_batches_normalises_and_sends_the_documented_body():
    route = respx.post(VOYAGE_URL).mock(side_effect=voyage_reply)
    ledger = Ledger()
    embedder = VoyageEmbedder("vk-test", ledger=ledger)
    texts = [f"review {i}" for i in range(205)]

    vectors = embedder.embed(texts)

    assert len(vectors) == 205 and embedder.dimensions == 1024
    assert all(len(v) == 1024 and abs(sum(x * x for x in v) - 1) < 1e-9 for v in vectors)
    sizes = [len(json.loads(call.request.content)["input"]) for call in route.calls]
    assert sizes == [100, 100, 5]
    first = route.calls[0].request
    assert first.headers["authorization"] == "Bearer vk-test"
    body = json.loads(first.content)
    assert body["model"] == "voyage-4-lite"
    assert body["input_type"] == "document" and body["output_dimension"] == 1024
    assert body["input"][:2] == ["review 0", "review 1"]
    # The reported token usage reaches the ledger, priced at USD 0.02 / 1M tokens.
    assert [r.usage.prompt_tokens for r in ledger.records] == [700, 700, 35]
    assert all(r.provider == "voyage" and r.model == "voyage-4-lite" for r in ledger.records)
    assert ledger.total_usd == pytest.approx(1435 * 0.02 / 1_000_000)


@respx.mock
def test_voyage_keeps_input_order_and_passes_query_type_and_dimension():
    route = respx.post(VOYAGE_URL).mock(side_effect=voyage_reply)
    embedder = VoyageEmbedder("vk-test", dimensions=512)
    vectors = embedder.embed(["a", "b", "c"], label="memory", input_type="query")
    assert len(vectors) == 3 and all(len(v) == 512 for v in vectors)
    body = json.loads(route.calls[0].request.content)
    assert body["input_type"] == "query" and body["output_dimension"] == 512


@respx.mock
def test_voyage_429_retries_with_retry_after_then_succeeds():
    route = respx.post(VOYAGE_URL)
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "3"}, json={"detail": "rate limited"}),
        voyage_reply,
    ]
    slept: list[float] = []
    embedder = VoyageEmbedder("vk-test", sleep=slept.append)
    assert len(embedder.embed(["one"])) == 1
    assert route.call_count == 2 and slept == [3.0]


@respx.mock
def test_voyage_persistent_429_is_transient_after_bounded_retries():
    route = respx.post(VOYAGE_URL).respond(429, json={"detail": "You have exceeded the RPM"})
    slept: list[float] = []
    ledger = Ledger()
    embedder = VoyageEmbedder("vk-test", ledger=ledger, sleep=slept.append, max_attempts=3)
    with pytest.raises(LLMTransientError) as info:
        embedder.embed(["one"])
    assert info.value.status == 429 and "exceeded" in str(info.value)
    assert route.call_count == 3 and slept == [2.0, 4.0]
    assert ledger.records[-1].error and ledger.records[-1].usage.prompt_tokens == 0


@respx.mock
def test_voyage_retry_wait_is_capped_by_max_wait():
    route = respx.post(VOYAGE_URL).respond(503, headers={"retry-after": "120"}, text="down")
    slept: list[float] = []
    embedder = VoyageEmbedder("vk-test", sleep=slept.append, max_wait_s=30)
    with pytest.raises(LLMTransientError) as info:
        embedder.embed(["one"])
    assert info.value.status == 503 and info.value.retry_after == 120
    assert route.call_count == 1 and slept == []  # waiting 120 s would blow the budget


@respx.mock
def test_voyage_transport_error_is_transient():
    respx.post(VOYAGE_URL).mock(side_effect=httpx.ConnectError("boom"))
    embedder = VoyageEmbedder("vk-test", sleep=lambda _: None)
    with pytest.raises(LLMTransientError):
        embedder.embed(["one"])


@respx.mock
def test_voyage_client_errors_are_permanent_and_not_retried():
    route = respx.post(VOYAGE_URL).respond(401, json={"detail": "Provided API key is invalid."})
    embedder = VoyageEmbedder("vk-bad", sleep=lambda _: pytest.fail("must not retry"))
    with pytest.raises(LLMPermanentError) as info:
        embedder.embed(["one"])
    assert info.value.status == 401 and route.call_count == 1


@respx.mock
def test_voyage_rejects_a_short_or_wrong_sized_response():
    respx.post(VOYAGE_URL).mock(side_effect=lambda request: voyage_reply(request, dims=768))
    with pytest.raises(LLMPermanentError, match="1024-d"):
        VoyageEmbedder("vk-test").embed(["one"])


def test_voyage_validates_key_and_dimension():
    with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
        VoyageEmbedder("")
    with pytest.raises(ValueError, match="768"):
        VoyageEmbedder("vk-test", dimensions=768)  # voyage-4-lite has no 768-d output


def test_voyage_respects_the_run_budget():
    from llm_kit import LLMBudgetError

    ledger = Ledger(max_calls=0)
    with pytest.raises(LLMBudgetError):
        VoyageEmbedder("vk-test", ledger=ledger).embed(["one"])


# ------------------------------------------------------------------ dimensions


@respx.mock
def test_gemini_sends_the_configured_dimension():
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents"
    ).mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={
                "embeddings": [
                    {"values": [1.0] * 1536} for _ in json.loads(request.content)["requests"]
                ]
            },
        )
    )
    embedder = GeminiEmbedder("g-test", dimensions=1536)
    vectors = embedder.embed(["a", "b"])
    assert embedder.dimensions == 1536 and all(len(v) == 1536 for v in vectors)
    requests = json.loads(route.calls[0].request.content)["requests"]
    assert {r["outputDimensionality"] for r in requests} == {1536}
    assert GeminiEmbedder("g-test").dimensions == 768
    with pytest.raises(ValueError):
        GeminiEmbedder("g-test", dimensions=4096)


def test_hash_embedder_takes_a_dimension():
    embedder = HashEmbedder(dimensions=1024)
    vector = embedder.embed(["crashes on launch"])[0]
    assert len(vector) == 1024 and embedder.model == "hash-1024"
    assert HashEmbedder().dimensions == 768 and HashEmbedder().model == "hash-768"


# ------------------------------------------------------------------ settings + factory


def test_settings_read_provider_key_and_dimension_from_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "Voyage")
    monkeypatch.setenv("VOYAGE_API_KEY", "vk-env")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "1024")
    settings = AppSettings(_env_file=None)
    assert settings.embedding_provider == "voyage" and settings.embedding_dimensions == 1024
    assert settings.has_key("voyage") and "vk-env" not in repr(settings)
    assert AppSettings(_env_file=None, embedding_provider="gemini").embedding_dimensions == 1024
    monkeypatch.delenv("EMBEDDING_DIMENSIONS")
    assert AppSettings(_env_file=None).embedding_dimensions == 768
    with pytest.raises(ValidationError):
        AppSettings(_env_file=None, embedding_provider="openai")


def test_factory_selects_by_provider_and_plumbs_the_dimension():
    ledger = Ledger()
    voyage = make_embedder(
        settings_for(embedding_provider="voyage", voyage_api_key="vk", embedding_dimensions=1024),
        ledger,
    )
    assert isinstance(voyage, VoyageEmbedder) and voyage.dimensions == 1024
    assert voyage.ledger is ledger

    gemini = make_embedder(settings_for(embedding_provider="gemini", gemini_api_key="g"), ledger)
    assert isinstance(gemini, GeminiEmbedder) and gemini.dimensions == 768

    hashed = make_embedder(settings_for(embedding_provider="hash", embedding_dimensions=1024))
    assert isinstance(hashed, HashEmbedder) and hashed.dimensions == 1024


def test_factory_validates_key_and_dimension():
    with pytest.raises(ValueError, match="VOYAGE_API_KEY"):
        make_embedder(settings_for(embedding_provider="voyage", embedding_dimensions=1024))
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        make_embedder(settings_for(embedding_provider="gemini"))
    with pytest.raises(ValueError, match="dimensions"):
        make_embedder(settings_for(embedding_provider="voyage", voyage_api_key="vk"))  # 768


def test_engine_degrades_without_a_key_and_builds_voyage_with_one():
    keyless = Engine.from_settings(settings_for(embedding_provider="voyage"))
    assert keyless.embedder_builder(Ledger()) is None  # embed + cluster skipped, not a crash
    engine = Engine.from_settings(
        settings_for(embedding_provider="voyage", voyage_api_key="vk", embedding_dimensions=1024)
    )
    embedder = engine.embedder_builder(Ledger())
    assert isinstance(embedder, VoyageEmbedder) and embedder.dimensions == 1024


# ------------------------------------------------------------------ re-embedding


def test_nulled_vectors_are_re_embedded_and_theme_centroids_restored(settings):
    """Migration 0002 sets every stored vector to NULL (a new model or dimension makes the
    old ones meaningless). The next run must re-embed the analysed reviews and give their
    themes a centroid again, or no new review could ever link to an existing theme."""
    engine = make_engine(settings)
    app = create_app(engine, store_kind="ios", store_id="1", name="Demo", github_repo="a/b")
    service = RunService(engine)
    assert service.process(service.create_run(app.id).id).status == "done"
    store = engine.store
    themes_before = {t.id for t in store.themes.values()}
    assert themes_before and all(t.embedding is not None for t in store.themes.values())

    for review_id, review in list(store.reviews.items()):  # what 0002 does to the tables
        store.reviews[review_id] = review.model_copy(update={"embedding": None})
    for theme_id, theme in list(store.themes.items()):
        store.themes[theme_id] = theme.model_copy(update={"embedding": None})
    owed = store.unprocessed_review_ids(app.id, 50)
    assert len(owed) == 11  # every analysed, non-quarantined review is queued again

    run = service.create_run(app.id)
    assert service.process(run.id).status == "done"
    assert all(store.reviews[i].embedding is not None for i in owed)
    assert {t.id for t in store.themes.values()} == themes_before  # no duplicate themes
    assert all(t.embedding is not None for t in store.themes.values())
    embed = next(s for s in store.list_steps(run.id) if s.name == "embed_reviews" and s.result)
    assert embed.result["centroids_restored"] == len(themes_before)


def test_migrations_render_the_configured_dimension():
    from review_radar.db.migrate import migration_files, render

    files = dict(migration_files())
    sql = render(files["0002_embedding_dimensions.sql"], 1024)
    assert "target constant integer := 1024;" in sql and "{{" not in sql
    assert all("{{" not in render(text, 768) for text in files.values())
