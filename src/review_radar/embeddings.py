"""Embeddings behind one small protocol, recorded in the same Ledger as every chat call.

    EmbeddingProvider       model, dimensions, embed(texts) -> unit vectors
    GeminiEmbedder          gemini-embedding-001 at 768 dimensions (native REST)
    HashEmbedder            deterministic bag-of-words hashing; offline tests and keyless runs

Why native REST for Gemini rather than llm-kit: llm-kit is a chat-completions layer and has
no embedding method, and Gemini's OpenAI-compatible `/embeddings` route does not take the
`taskType` field. The call still goes through the project's provider layer (this module):
it is budget-checked against the run's Ledger before it is sent and recorded in it after,
so embedding spend appears in the same cost report as everything else.

A corpus is tied to one embedding model: vectors from different models (or dimensions)
are not comparable, so `reviews.embedding` is `vector(768)` and switching models means
re-embedding. Gemini returns unnormalised vectors below 3072 dimensions; we normalise, so
a dot product is a cosine similarity everywhere downstream.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import time
from typing import Protocol

import httpx
from llm_kit import CallRecord, Ledger, LLMBudgetError, LLMPermanentError, LLMTransientError
from llm_kit import Usage as LLMUsage
from llm_kit.pricing import PRICES, ModelPrice

DIMENSIONS = 768
GEMINI_MODEL = "gemini-embedding-001"
GEMINI_BATCH = 100  # batchEmbedContents accepts up to 100 requests per call

# llm-kit's price table (verified 2026-09-05) has no row for this model, and an unpriced
# model is charged at the worst known rate and flagged. Registering the published paid
# rate (USD 0.15 per 1M input tokens, output free) keeps the cost report honest.
PRICES.setdefault(GEMINI_MODEL, ModelPrice(0.15, 0.0, free_tier=True, note="embeddings"))


def normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


class EmbeddingProvider(Protocol):
    model: str
    dimensions: int

    def embed(self, texts: list[str], *, label: str = "embed") -> list[list[float]]: ...


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
    """Feature hashing of word unigrams and bigrams into 768 buckets, normalised.

    Not semantic: "crash" and "freezes" share nothing. It exists so the whole workflow
    (embed -> cluster -> link to memory) runs offline and deterministically in tests and
    in a keyless local demo. Reviews that share words do land close together, which is
    enough to exercise the clustering code paths."""

    model = "hash-768"
    dimensions = DIMENSIONS

    def __init__(self, ledger: Ledger | None = None):
        self.ledger = ledger

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

    def embed(self, texts: list[str], *, label: str = "embed") -> list[list[float]]:
        return [self._vector(text) for text in texts]


class GeminiEmbedder:
    model = GEMINI_MODEL
    dimensions = DIMENSIONS

    def __init__(
        self,
        api_key: str,
        *,
        ledger: Ledger | None = None,
        task_type: str = "CLUSTERING",
        client: httpx.Client | None = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for GeminiEmbedder")
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

    def embed(self, texts: list[str], *, label: str = "embed") -> list[list[float]]:
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


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))
