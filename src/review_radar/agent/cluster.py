"""Stage 4, cluster: memory first, then agglomerative clustering, then model-written names.

    1. Link. Each new review is compared with the centroids of the app's existing themes
       (pgvector nearest neighbour). Cosine >= `link_threshold` joins that theme: memory
       before invention, so "the shuffle bug" stays one theme across a month of runs.
    2. Cluster. The reviews left over are grouped by average-linkage agglomerative
       clustering over cosine similarity: repeatedly merge the two clusters whose mean
       pairwise similarity is highest, until no pair reaches `merge_threshold`. Clusters
       of at least `min_cluster_size` become new themes; singletons stay unthemed (they
       can still get a reply).
    3. Name. One model call names every new cluster; quotes are kept only if verbatim.

Why agglomerative rather than k-means: the number of themes is not known in advance, and
a similarity threshold ("these are about the same thing") is something a person can
reason about and tune against hand labels (docs/evals.md sweeps it). Why average linkage:
single linkage chains unrelated reviews through one ambiguous middle review; complete
linkage splits a real theme because two members phrase it differently. n is at most a few
hundred per run, so the O(n^3) textbook algorithm on a numpy similarity matrix is fine.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel

from ..models import Review, ThemeQuote
from ..prompts import wrap_untrusted
from .guardrails import is_verbatim

MAX_MEMBERS_IN_PROMPT = 8
MAX_CHARS_PER_MEMBER = 400


def similarity_matrix(vectors: list[list[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = matrix / norms
    return unit @ unit.T


def agglomerate(vectors: list[list[float]], threshold: float) -> list[list[int]]:
    """Average-linkage agglomerative clustering on cosine similarity.

    Returns clusters as lists of input indices, largest first, members in input order."""
    n = len(vectors)
    if n == 0:
        return []
    if n == 1:
        return [[0]]
    sim = similarity_matrix(vectors)
    clusters: dict[int, list[int]] = {i: [i] for i in range(n)}
    # link[a, b] = average similarity between clusters a and b (kept for live ids only).
    link = sim.copy()
    np.fill_diagonal(link, -np.inf)
    active = np.ones(n, dtype=bool)
    while True:
        masked = np.where(np.outer(active, active), link, -np.inf)
        a, b = np.unravel_index(int(np.argmax(masked)), masked.shape)
        if masked[a, b] < threshold:
            break
        a, b = (int(a), int(b)) if a < b else (int(b), int(a))
        size_a, size_b = len(clusters[a]), len(clusters[b])
        # Lance-Williams update for average linkage.
        merged = (size_a * link[a] + size_b * link[b]) / (size_a + size_b)
        link[a, :] = merged
        link[:, a] = merged
        link[a, a] = -np.inf
        link[b, :] = -np.inf
        link[:, b] = -np.inf
        active[b] = False
        clusters[a] = sorted(clusters[a] + clusters.pop(b))
    return sorted(clusters.values(), key=lambda members: (-len(members), members[0]))


def centroid(vectors: list[list[float]]) -> list[float]:
    mean = np.asarray(vectors, dtype=np.float64).mean(axis=0)
    norm = np.linalg.norm(mean)
    return (mean / norm if norm else mean).tolist()


def updated_centroid(old: list[float], old_count: int, new: list[list[float]]) -> list[float]:
    """Running centroid: the old centroid weighted by its member count plus the new members."""
    total = np.asarray(old, dtype=np.float64) * max(old_count, 1) + np.asarray(new).sum(axis=0)
    norm = np.linalg.norm(total)
    return (total / norm if norm else total).tolist()


class ThemeQuoteOut(BaseModel):
    review_id: str
    text: str


class ThemeName(BaseModel):
    # `name`, not `title`: llm-kit's schema normaliser deletes any key called "title",
    # including a property of that name, so a `title` field can never be generated.
    ref: str
    name: str
    summary: str
    quotes: list[ThemeQuoteOut]


class ThemeNameBatch(BaseModel):
    themes: list[ThemeName]


def build_naming_message(
    clusters: list[list[Review]],
) -> tuple[str, dict[str, list[Review]], dict[str, Review]]:
    """Clusters get refs c1..cN and members short ids c1m1..; ids map back to reviews."""
    by_ref: dict[str, list[Review]] = {}
    by_short: dict[str, Review] = {}
    parts = []
    for c_index, members in enumerate(clusters, start=1):
        ref = f"c{c_index}"
        by_ref[ref] = members
        inner = []
        for m_index, review in enumerate(members[:MAX_MEMBERS_IN_PROMPT], start=1):
            short = f"{ref}m{m_index}"
            by_short[short] = review
            inner.append(wrap_untrusted("review", review.text[:MAX_CHARS_PER_MEMBER], id=short))
        parts.append(
            f'<theme ref="{ref}" members="{len(members)}">\n' + "\n".join(inner) + "\n</theme>"
        )
    header = f"Name these {len(clusters)} clusters of reviews.\n\n"
    return header + "\n\n".join(parts), by_ref, by_short


def fallback_name(members: list[Review], kind: str, area: str | None) -> tuple[str, str]:
    """Deterministic name when the model's output is unusable: never block a run on naming."""
    label = (area or kind).strip().capitalize()
    title = f"{label}: {len(members)} related reviews"
    first = members[0].text.strip().replace("\n", " ")
    summary = f"{len(members)} reviews about {area or kind}, for example: “{first[:160]}”"
    return title[:120], summary[:300]


def verified_quotes(name: ThemeName, by_short: dict[str, Review]) -> list[ThemeQuote]:
    quotes = []
    for quote in name.quotes:
        review = by_short.get(quote.review_id.strip())
        if review is not None and is_verbatim(quote.text, review.text):
            quotes.append(ThemeQuote(review_id=review.id, text=quote.text.strip()[:240]))
    return quotes[:3]
