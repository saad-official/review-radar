"""Wiring: settings + routing -> the collaborators a run needs. Shared by API, demo, evals.

`Engine` holds the long-lived, swappable pieces (store, how to build the three model tiers,
the embedder, a review source, a GitHub client). Tests construct it with fakes; production
uses `Engine.from_settings`. Nothing in the workflow imports settings directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from llm_kit import Ledger

from .config import AppSettings
from .db import Store, make_store
from .embeddings import EmbeddingProvider, GeminiEmbedder, HashEmbedder
from .executors.github import GitHubIssues
from .ingest.base import ReviewSource
from .llm import RoutedLLM, TokenPacer
from .models import App
from .routing import Routing, load_routing

LLMBuilder = Callable[[Ledger], tuple[Any, Any, Any]]  # (extract, theme, agent)
EmbedderBuilder = Callable[[Ledger], EmbeddingProvider | None]
SourceBuilder = Callable[[App], ReviewSource | None]
GitHubBuilder = Callable[[str], GitHubIssues]


def default_source(app: App) -> ReviewSource | None:
    from .ingest import source_for

    return source_for(app.store)


@dataclass
class Engine:
    settings: AppSettings
    store: Store
    routing: Routing
    llm_builder: LLMBuilder
    embedder_builder: EmbedderBuilder
    source_builder: SourceBuilder = default_source
    github_builder: GitHubBuilder = GitHubIssues
    pacer: TokenPacer = field(default_factory=TokenPacer)

    @property
    def budget_usd(self) -> float:
        return self.settings.max_usd_per_run or self.routing.budget.max_usd_per_run

    @classmethod
    def from_settings(
        cls, settings: AppSettings, store: Store | None = None, routing: Routing | None = None
    ) -> Engine:
        routing = routing or load_routing()
        store = store if store is not None else make_store(settings)
        # One pacer per process: Groq's tokens-per-minute limit is per account and model.
        pacer = TokenPacer()
        llm_settings = settings.llm_settings()
        retry = routing.retry.policy()

        def build_llms(ledger: Ledger) -> tuple[RoutedLLM, RoutedLLM, RoutedLLM]:
            common = {"ledger": ledger, "retry": retry, "settings": llm_settings, "pacer": pacer}
            return (
                RoutedLLM("extract", routing.tier("extract"), **common),
                RoutedLLM("theme", routing.tier("theme"), **common),
                RoutedLLM("agent", routing.tier("agent"), **common),
            )

        def build_embedder(ledger: Ledger) -> EmbeddingProvider | None:
            if settings.embedding_provider == "hash":
                return HashEmbedder(ledger)
            if settings.has_key("gemini"):
                return GeminiEmbedder(settings.secret(settings.gemini_api_key), ledger=ledger)
            return None  # embed and cluster are skipped, with a note in the trajectory

        return cls(
            settings=settings,
            store=store,
            routing=routing,
            llm_builder=build_llms,
            embedder_builder=build_embedder,
            pacer=pacer,
        )
