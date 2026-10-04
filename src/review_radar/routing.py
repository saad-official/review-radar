"""Load `routing.toml` into typed objects. The table is config, not code, so a model swap
is a one-line reviewed diff and every run stores the routing it used (`runs.routing`)."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from llm_kit import RetryPolicy, price_for
from pydantic import BaseModel, Field


class Route(BaseModel):
    provider: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.2
    reasoning_effort: str | None = None
    tpm_limit: int | None = None
    # Extra draws on the same route when the provider rejects output against the strict
    # schema (Groq's 400 json_validate_failed). Cheaper than jumping to the fallback.
    schema_retries: int = 0

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


class Tier(BaseModel):
    provider: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.2
    reasoning_effort: str | None = None
    tpm_limit: int | None = None
    schema_retries: int = 0
    fallbacks: list[Route] = Field(default_factory=list)

    @property
    def routes(self) -> list[Route]:
        primary = Route(**self.model_dump(exclude={"fallbacks"}))
        return [primary, *self.fallbacks]


class RetryConfig(BaseModel):
    max_attempts: int = 3
    max_delay_s: float = 20.0
    deadline_s: float = 45.0

    def policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.max_attempts, max_delay_s=self.max_delay_s, deadline_s=self.deadline_s
        )


class Budget(BaseModel):
    max_usd_per_run: float = 0.10


class AgentConfig(BaseModel):
    """Bounds on the one open-ended step (the propose loop). See decision 0001."""

    max_iterations: int = 24
    deadline_s: float = 200.0
    # Below this many seconds left in a /process request, the propose stage is not
    # started; the run suspends and the next call starts it with a full window.
    min_window_s: float = 60.0


class ClusterConfig(BaseModel):
    # Cosine similarity to an existing theme's centroid needed to join it.
    link_threshold: float = 0.80
    # Average-linkage cosine similarity needed to merge two clusters into a new theme.
    merge_threshold: float = 0.78
    min_cluster_size: int = 2
    # Cluster only within a theme kind (bug, request, ...). Measured in docs/evals.md.
    within_kind: bool = True


class Routing(BaseModel):
    version: str
    budget: Budget = Field(default_factory=Budget)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    cluster: ClusterConfig = Field(default_factory=ClusterConfig)
    prompts: dict[str, str] = Field(default_factory=dict)
    tiers: dict[str, Tier]

    def tier(self, name: str) -> Tier:
        if name not in self.tiers:
            raise KeyError(f"routing.toml has no tier {name!r}")
        return self.tiers[name]

    def unpriced_models(self) -> list[str]:
        """Models llm-kit has no price for. A routed model without a price would be charged
        at the worst known rate and flagged; CI asserts this list is empty instead."""
        return [
            route.model
            for tier in self.tiers.values()
            for route in tier.routes
            if not price_for(route.model)[1]
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tiers": {
                name: [route.key for route in tier.routes] for name, tier in self.tiers.items()
            },
            "budget_usd": self.budget.max_usd_per_run,
        }


@lru_cache
def load_routing(path: str | None = None) -> Routing:
    if path:
        raw = Path(path).read_text(encoding="utf-8")
    else:
        raw = (resources.files("review_radar") / "routing.toml").read_text(encoding="utf-8")
    return Routing.model_validate(tomllib.loads(raw))
