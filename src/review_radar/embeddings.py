"""Embeddings behind one small protocol, recorded in the same Ledger as every chat call.

    EmbeddingProvider       model, dimensions, embed(texts) -> unit vectors
    GeminiEmbedder          gemini-embedding-001, 768 dimensions by default (native REST)
    VoyageEmbedder          voyage-4-lite, 1024 dimensions by default (256/512/1024/2048)
    HashEmbedder            deterministic bag-of-words hashing; offline tests and keyless runs
    make_embedder           the configured one (EMBEDDING_PROVIDER, EMBEDDING_DIMENSIONS)

Why native REST rather than llm-kit: llm-kit is a chat-completions layer and has no
embedding method, Gemini's OpenAI-compatible `/embeddings` route does not take the
`taskType` field, and Voyage has no OpenAI-compatible endpoint at all. Every call still
goes through the project's provider layer (this module): it is budget-checked against the
run's Ledger before it is sent and recorded in it after, so embedding spend appears in the
same cost report as everything else.

A corpus is tied to one embedding model: vectors from different models (or dimensions)
are not comparable, so `reviews.embedding` is `vector(EMBEDDING_DIMENSIONS)` and switching
models means re-embedding (docs/decisions/0005-voyage-embeddings.md). Gemini returns
unnormalised vectors below 3072 dimensions; every vector is normalised here, so a dot
product is a cosine similarity everywhere downstream.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

import httpx
from llm_kit import CallRecord, Ledger, LLMBudgetError, LLMPermanentError, LLMTransientError
from llm_kit import Usage as LLMUsage
from llm_kit.pricing import PRICES, ModelPrice

InputType = Literal["document", "query"]

# The vector columns' dimension is a deployment choice (EMBEDDING_DIMENSIONS, rendered into
# the migrations by `uv run migrate`); these are each embedder's default when none is passed.
DEFAULT_DIMENSIONS = 768
GEMINI_MODEL = "gemini-embedding-001"
GEMINI_BATCH = 100  # batchEmbedContents accepts up to 100 requests per call
GEMINI_DIMENSIONS = 768  # Matryoshka: any value 128..3072 (768/1536/3072 recommended)
VOYAGE_MODEL = "voyage-4-lite"
VOYAGE_BATCH = 100  # the API takes up to 1,000 texts per call; 100 keeps calls small
VOYAGE_DIMENSIONS = 1024
VOYAGE_ALLOWED_DIMENSIONS = (256, 512, 1024, 2048)

# llm-kit's price table (verified 2026-09-05) has no row for this model, and an unpriced
# model is charged at the worst known rate and flagged. Registering the published paid
# rate (USD 0.15 per 1M input tokens, output free) keeps the cost report honest.
PRICES.setdefault(GEMINI_MODEL, ModelPrice(0.15, 0.0, free_tier=True, note="embeddings"))
# Voyage: USD 0.02 per 1M tokens after the account's 200M free tokens (checked 2026-10-04).
PRICES.setdefault(VOYAGE_MODEL, ModelPrice(0.02, 0.0, free_tier=True, note="embeddings"))


def normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


class EmbeddingProvider(Protocol):
    model: str
    dimensions: int

    def embed(
        self, texts: list[str], *, label: str = "embed", input_type: InputType = "document"
    ) -> list[list[float]]:
        """`input_type` is "query" for a memory search and "document" for everything that is
        stored (reviews, theme centroids). Only Voyage uses it (it prepends a retrieval
        prompt); Gemini embeds everything with one task type and hashing ignores it."""
        ...


_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "i'm",
        "in",
        "is",
        "it",
        "it's",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "so",
        "that",
        "the",
        "this",
        "to",
        "was",
        "we",
        "with",
        "you",
        "your",
        "they",
        "them",
        "just",
        "very",
        "can",
        "can't",
        "cant",
        "not",
        "no",
        "do",
        "don't",
        "dont",
        "app",
    ]
)


class HashEmbedder:
    """Feature hashing of word unigrams and bigrams into `dimensions` buckets, normalised.

    Not semantic: "crash" and "freezes" share nothing. It exists so the whole workflow
    (embed -> cluster -> link to memory) runs offline and deterministically in tests and
    in a keyless local demo. Reviews that share words do land close together, which is
    enough to exercise the clustering code paths."""

    def __init__(self, ledger: Ledger | None = None, *, dimensions: int = DEFAULT_DIMENSIONS):
        self.ledger = ledger
        self.dimensions = dimensions
        self.model = f"hash-{dimensions}"

    def _vector(self, text: str) -> list[float]:
        words = [w for w in _WORD.findall(text.lower()) if w not in _STOP]
        features = words + [f"{a} {b}" for a, b in itertools.pairwise(words)]
        vector = [0.0] * self.dimensions
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        if not any(vector):
            vector[0] = 1.0
        return normalise(vector)

    def embed(
        self, texts: list[str], *, label: str = "embed", input_type: InputType = "document"
    ) -> list[list[float]]:
        return [self._vector(text) for text in texts]


class GeminiEmbedder:
    model = GEMINI_MODEL

    def __init__(
        self,
        api_key: str,
        *,
        dimensions: int = GEMINI_DIMENSIONS,
        ledger: Ledger | None = None,
        task_type: str = "CLUSTERING",
        client: httpx.Client | None = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for GeminiEmbedder")
        if not 128 <= dimensions <= 3072:
            raise ValueError(f"{GEMINI_MODEL} supports 128..3072 dimensions, not {dimensions}")
        self.dimensions = dimensions
        self._key = api_key
        self.ledger = ledger if ledger is not None else Ledger()
        # One task type for reviews, theme centroids and memory queries alike, so every
        # vector lives in the same space (CLUSTERING is tuned for grouping similar texts).
        self.task_type = task_type
        self._http = client or httpx.Client(timeout=30.0)
        self._base = base_url.rstrip("/")

    def _post(self, texts: list[str]) -> list[list[float]]:
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": text[:8000]}]},
                    "taskType": self.task_type,
                    "outputDimensionality": self.dimensions,
                }
                for text in texts
            ]
        }
        try:
            response = self._http.post(
                f"{self._base}/models/{self.model}:batchEmbedContents",
                headers={"x-goog-api-key": self._key},
                json=body,
            )
        except httpx.TransportError as exc:
            raise LLMTransientError(f"embedding transport failure: {exc}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise LLMTransientError(
                f"{response.status_code} from embeddings: {response.text[:800]}",
                status=response.status_code,
            )
        if response.status_code >= 400:
            raise LLMPermanentError(
                f"{response.status_code} from embeddings: {response.text[:800]}",
                status=response.status_code,
            )
        embeddings = response.json().get("embeddings", [])
        if len(embeddings) != len(texts):
            raise LLMPermanentError(f"expected {len(texts)} embeddings, got {len(embeddings)}")
        return [normalise([float(v) for v in item["values"]]) for item in embeddings]

    def embed(
        self, texts: list[str], *, label: str = "embed", input_type: InputType = "document"
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), GEMINI_BATCH):
            batch = texts[start : start + GEMINI_BATCH]
            blocked = self.ledger.would_exceed()
            if blocked is not None:
                raise LLMBudgetError(blocked)
            began = time.monotonic()
            # The API reports no token usage for embeddings; ~4 characters per token is
            # the documented rule of thumb, and the label says it is an estimate.
            tokens = sum(len(text[:8000]) // 4 + 1 for text in batch)
            try:
                vectors.extend(self._post(batch))
            except Exception as exc:
                self.ledger.add(
                    CallRecord(
                        provider="gemini",
                        model=self.model,
                        label=f"{label}:estimated",
                        usage=LLMUsage(),
                        latency_s=time.monotonic() - began,
                        error=f"{type(exc).__name__}: {exc}"[:500],
                    )
                )
                raise
            self.ledger.add(
                CallRecord(
                    provider="gemini",
                    model=self.model,
                    label=f"{label}:estimated",
                    usage=LLMUsage(tokens, 0, 0),
                    latency_s=time.monotonic() - began,
                    finish_reason="stop",
                )
            )
        return vectors


class VoyageEmbedder:
    """voyage-4-lite over Voyage's REST API (`POST /v1/embeddings`).

    Differences from Gemini that matter here: Voyage reports `usage.total_tokens`, so the
    ledger records real token counts (no ":estimated" label); `input_type` is honoured
    ("document" for reviews and theme centroids, "query" for memory searches; Voyage puts
    both in one space, the query side with a retrieval prompt); and only 256/512/1024/2048
    dimensions exist, so the vector columns are 1024-d when this provider is used.

    429 and 5xx are transient. Voyage's free tier without a payment method allows only a
    few requests per minute, so a short bounded retry (honouring Retry-After, at most
    `max_wait_s` of sleeping in total) absorbs a burst; past that the call raises
    LLMTransientError and the run fails cleanly with the reviews still queued, exactly
    like a Gemini quota error.
    """

    model = VOYAGE_MODEL
    provider = "voyage"

    def __init__(
        self,
        api_key: str,
        *,
        dimensions: int = VOYAGE_DIMENSIONS,
        ledger: Ledger | None = None,
        client: httpx.Client | None = None,
        base_url: str = "https://api.voyageai.com/v1",
        max_attempts: int = 3,
        max_wait_s: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key:
            raise ValueError("VOYAGE_API_KEY is required for VoyageEmbedder")
        if dimensions not in VOYAGE_ALLOWED_DIMENSIONS:
            raise ValueError(
                f"{VOYAGE_MODEL} supports {VOYAGE_ALLOWED_DIMENSIONS} dimensions, not {dimensions}"
            )
        self._key = api_key
        self.dimensions = dimensions
        self.ledger = ledger if ledger is not None else Ledger()
        self._http = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))
        self._base = base_url.rstrip("/")
        self.max_attempts = max(1, max_attempts)
        self.max_wait_s = max_wait_s
        self._sleep = sleep

    def _once(self, texts: list[str], input_type: InputType) -> tuple[list[list[float]], int]:
        try:
            response = self._http.post(
                f"{self._base}/embeddings",
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "input": texts,
                    "model": self.model,
                    "input_type": input_type,
                    "output_dimension": self.dimensions,
                },
            )
        except httpx.TransportError as exc:
            raise LLMTransientError(f"embedding transport failure: {exc}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise LLMTransientError(
                f"{response.status_code} from voyage embeddings: {response.text[:800]}",
                status=response.status_code,
                retry_after=_retry_after(response),
            )
        if response.status_code >= 400:
            raise LLMPermanentError(
                f"{response.status_code} from voyage embeddings: {response.text[:800]}",
                status=response.status_code,
            )
        body: dict[str, Any] = response.json()
        data = sorted(body.get("data") or [], key=lambda item: item.get("index", 0))
        if len(data) != len(texts):
            raise LLMPermanentError(f"expected {len(texts)} embeddings, got {len(data)}")
        vectors = [normalise([float(v) for v in item["embedding"]]) for item in data]
        if any(len(vector) != self.dimensions for vector in vectors):
            raise LLMPermanentError(f"expected {self.dimensions}-d vectors from {self.model}")
        return vectors, int((body.get("usage") or {}).get("total_tokens") or 0)

    def _post(self, texts: list[str], input_type: InputType) -> tuple[list[list[float]], int]:
        waited = 0.0
        attempt = 1
        while True:
            try:
                return self._once(texts, input_type)
            except LLMTransientError as exc:
                delay = exc.retry_after if exc.retry_after is not None else 2.0**attempt
                if attempt >= self.max_attempts or waited + delay > self.max_wait_s:
                    raise
                waited += delay
                attempt += 1
                self._sleep(delay)

    def embed(
        self, texts: list[str], *, label: str = "embed", input_type: InputType = "document"
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), VOYAGE_BATCH):
            batch = [text[:8000] for text in texts[start : start + VOYAGE_BATCH]]
            blocked = self.ledger.would_exceed()
            if blocked is not None:
                raise LLMBudgetError(blocked)
            began = time.monotonic()
            try:
                batch_vectors, tokens = self._post(batch, input_type)
            except Exception as exc:
                self.ledger.add(
                    CallRecord(
                        provider=self.provider,
                        model=self.model,
                        label=label,
                        usage=LLMUsage(),
                        latency_s=time.monotonic() - began,
                        error=f"{type(exc).__name__}: {exc}"[:500],
                    )
                )
                raise
            vectors.extend(batch_vectors)
            self.ledger.add(
                CallRecord(
                    provider=self.provider,
                    model=self.model,
                    label=label,
                    usage=LLMUsage(tokens, 0, 0),
                    latency_s=time.monotonic() - began,
                    finish_reason="stop",
                )
            )
        return vectors


def _retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("retry-after")
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        return None


def make_embedder(settings: Any, ledger: Ledger | None = None) -> EmbeddingProvider:
    """The embedder EMBEDDING_PROVIDER names, at EMBEDDING_DIMENSIONS.

    Raises ValueError when the provider's key is missing or the dimension is one the model
    does not support. `Engine.from_settings` turns a missing key into "no embedder" (embed
    and cluster are skipped with a note in the trajectory), so a keyless deploy degrades
    instead of crashing; scripts that need real vectors call this and fail loudly.
    """
    name = settings.embedding_provider
    dimensions = settings.embedding_dimensions
    if name == "hash":
        return HashEmbedder(ledger, dimensions=dimensions)
    if name == "voyage":
        if not settings.has_key("voyage"):
            raise ValueError("EMBEDDING_PROVIDER=voyage but VOYAGE_API_KEY is not set")
        return VoyageEmbedder(
            settings.secret(settings.voyage_api_key), dimensions=dimensions, ledger=ledger
        )
    if name == "gemini":
        if not settings.has_key("gemini"):
            raise ValueError("EMBEDDING_PROVIDER=gemini but GEMINI_API_KEY is not set")
        return GeminiEmbedder(
            settings.secret(settings.gemini_api_key), dimensions=dimensions, ledger=ledger
        )
    raise ValueError(f"unknown EMBEDDING_PROVIDER {name!r} (gemini, voyage or hash)")


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))
