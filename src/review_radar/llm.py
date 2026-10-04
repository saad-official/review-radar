"""The project's thin adapter over llm-kit: fallback routes, pacing, quota cooldown.

Copied from Changelog Forge (same commit of llm-kit, same lessons) and extended with the
one thing this project needs that Changelog Forge did not: a routed `call_tools` for the
propose loop. llm-kit gives one `LLM` per (provider, model) with retries, a budget check
and a ledger; what stays here ("llm-kit knows about providers, projects know about
problems"):

  1. Fallback across providers. When a route is rate-limited past its retry deadline, a
     model id is withdrawn, or the output will not validate even after the repair attempt,
     the same call is re-issued on the next route in `routing.toml`. A budget error is
     never a reason to fall back: it is our own ceiling doing its job.
  2. Token-per-minute pacing for Groq's free tier (8,000 TPM per model).
  3. Daily-quota cooldown. A "per day" 429 can never succeed by retrying; the route is
     parked until the reset instead of burning ~40 s of retries on every call.
  4. `max_retries=0` on the OpenAI SDK, so llm-kit's retry layer is the only one (two
     invisible retry layers outlived the retry deadline in Changelog Forge's first eval).
  5. Gemini thought signatures in tool loops (see `_SignatureShim`).
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from typing import Any, Protocol, TypeVar

from llm_kit import (
    LLM,
    AgentResult,
    Ledger,
    LLMBudgetError,
    LLMError,
    LLMOutputError,
    LLMPermanentError,
    LLMTransientError,
    RetryPolicy,
    Tool,
)
from llm_kit import Settings as LLMSettings
from pydantic import BaseModel

from .routing import Route, Tier

TModel = TypeVar("TModel", bound=BaseModel)

EXPECTED_OUTPUT_TOKENS = 1500
WINDOW_S = 60.0


def estimate_tokens(text: str) -> int:
    """~4 characters per token: close enough for pacing, which only needs an upper bound."""
    return len(text) // 4 + 1


def is_schema_rejection(exc: Exception) -> bool:
    """Groq answers an off-schema generation with a 400 ("Generated JSON does not match the
    expected schema"). llm-kit rightly calls a 400 permanent, but this one is a sampling
    failure: the same request usually succeeds on a second draw."""
    text = str(exc)
    return isinstance(exc, LLMPermanentError) and (
        "does not match the expected schema" in text or "json_validate_failed" in text
    )


_DAILY = re.compile(r"per day \((?:TPD|RPD)\)|PerDay|daily", re.IGNORECASE)
_TRY_AGAIN = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.IGNORECASE)


def daily_quota_reset_s(exc: Exception) -> float | None:
    """Seconds until a *daily* quota resets, if `exc` is a daily-quota 429 (else None).

    Groq says "Limit ... per day (TPD) ... try again in 18m11s"; Gemini says
    "...PerDay..." in its quota id. Without a parsable wait, assume an hour."""
    text = str(exc)
    if not isinstance(exc, LLMTransientError) or not _DAILY.search(text):
        return None
    match = _TRY_AGAIN.search(text)
    if not match or not any(match.groups()):
        return 3600.0
    hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def is_quota_error(exc: Exception) -> bool:
    """Any rate or quota rejection (429 or RESOURCE_EXHAUSTED), daily or not."""
    text = str(exc)
    return isinstance(exc, LLMTransientError) and (
        "429" in text or "RESOURCE_EXHAUSTED" in text or "rate limit" in text.lower()
    )


class LLMRouteError(LLMError):
    """Every route for a tier failed. Carries each route's reason, in order."""

    def __init__(self, tier: str, reasons: list[str]):
        super().__init__(f"all routes for tier {tier!r} failed: " + " | ".join(reasons))
        self.reasons = reasons

    @property
    def quota_exhausted(self) -> bool:
        return bool(self.reasons) and all(
            "quota" in reason.lower() or "429" in reason or "unavailable" in reason
            for reason in self.reasons
        )


class StructuredLLM(Protocol):
    """What the workflow needs from a model. RoutedLLM implements it; tests fake it."""

    def complete_structured(
        self,
        messages: str | list[dict[str, Any]],
        schema: type[TModel],
        *,
        system: str | None = None,
        label: str | None = None,
    ) -> TModel: ...


class ToolsLLM(Protocol):
    def call_tools(
        self,
        messages: str | list[dict[str, Any]],
        tools: Sequence[Tool],
        *,
        system: str | None = None,
        label: str | None = None,
        max_iterations: int = 6,
        deadline_s: float = 120.0,
        on_step: Callable[[int, Any, list[Any]], None] | None = None,
    ) -> AgentResult: ...


class TokenPacer:
    """Sliding-window tokens-per-minute guard, one window per model, plus daily cooldowns."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._clock = clock
        self._sleep = sleep
        self._windows: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._exhausted: dict[str, float] = {}
        self.slept_s = 0.0

    def _used(self, model: str, now: float) -> int:
        window = self._windows[model]
        while window and now - window[0][0] >= WINDOW_S:
            window.popleft()
        return sum(tokens for _, tokens in window)

    def wait(self, model: str, limit: int | None, estimate: int) -> float:
        if not limit:
            return 0.0
        now = self._clock()
        used = self._used(model, now)
        if used + estimate <= limit:
            return 0.0
        excess = used + estimate - limit
        freed, wait_until = 0, now
        for at, tokens in self._windows[model]:
            freed += tokens
            wait_until = at + WINDOW_S
            if freed >= excess:
                break
        waited = max(0.0, wait_until - now)
        if waited > 0:
            self._sleep(waited)
            self.slept_s += waited
        return waited

    def mark_exhausted(self, model: str, seconds: float) -> None:
        self._exhausted[model] = self._clock() + seconds

    def exhausted_for(self, model: str) -> float:
        return max(0.0, self._exhausted.get(model, 0.0) - self._clock())

    def record(self, model: str, tokens: int) -> None:
        if tokens > 0:
            self._windows[model].append((self._clock(), tokens))


class _SignatureCompletions:
    def __init__(self, inner: Any, cache: dict[str, Any]):
        self._inner = inner
        self._cache = cache

    def _restore(self, message: Any) -> Any:
        if not isinstance(message, dict) or not message.get("tool_calls") or not self._cache:
            return message
        calls = []
        for call in message["tool_calls"]:
            extra = self._cache.get(call.get("id", ""))
            calls.append(
                {**call, "extra_content": extra} if extra and "extra_content" not in call else call
            )
        return {**message, "tool_calls": calls}

    def create(self, **kwargs: Any) -> Any:
        if "messages" in kwargs:
            kwargs["messages"] = [self._restore(message) for message in kwargs["messages"]]
        raw = self._inner.create(**kwargs)
        for choice in getattr(raw, "choices", None) or []:
            for call in getattr(choice.message, "tool_calls", None) or []:
                extra = (getattr(call, "model_extra", None) or {}).get("extra_content")
                if extra and getattr(call, "id", None):
                    self._cache[call.id] = extra
        return raw


class _SignatureShim:
    """Gemini's OpenAI-compatible endpoint attaches a `thought_signature` to each tool call
    (in `extra_content`) and rejects the next turn if the assistant message comes back
    without it. llm-kit's loop rebuilds the assistant message from id/type/function only,
    which drops it. This proxy remembers each call id's `extra_content` from responses and
    puts it back on the way out. It is a no-op for providers that send no extra content."""

    def __init__(self, client: Any):
        self._client = client
        self._cache: dict[str, Any] = {}
        self.chat = type("Chat", (), {})()
        self.chat.completions = _SignatureCompletions(client.chat.completions, self._cache)

    def with_options(self, **kwargs: Any) -> _SignatureShim:
        return _SignatureShim(self._client.with_options(**kwargs))


LLMFactory = Callable[[Route, Ledger, RetryPolicy, LLMSettings | None], Any]


def default_factory(
    route: Route, ledger: Ledger, retry: RetryPolicy, settings: LLMSettings | None
) -> LLM:
    llm = LLM(
        provider=route.provider,
        model=route.model,
        settings=settings,
        ledger=ledger,
        retry_policy=retry,
        temperature=route.temperature,
        max_tokens=route.max_tokens,
        label=route.model,
    )
    llm.client = llm.client.with_options(max_retries=0)
    if route.provider == "gemini":
        llm.client = _SignatureShim(llm.client)
    return llm


class RoutedLLM:
    """A tier from routing.toml: the primary route, then each fallback, sharing one Ledger."""

    def __init__(
        self,
        name: str,
        tier: Tier,
        *,
        ledger: Ledger,
        retry: RetryPolicy | None = None,
        settings: LLMSettings | None = None,
        factory: LLMFactory = default_factory,
        pacer: TokenPacer | None = None,
    ):
        self.name = name
        self.tier = tier
        self.ledger = ledger
        self.retry = retry or RetryPolicy()
        self.settings = settings
        self.factory = factory
        self.pacer = pacer or TokenPacer()
        self._clients: dict[str, Any] = {}
        self.served_by: list[str] = []
        self.fallback_reasons: list[str] = []

    @property
    def model(self) -> str:
        return self.served_by[-1] if self.served_by else self.tier.model

    def _client(self, route: Route) -> Any:
        if route.key not in self._clients:
            # ValueError when the provider's key is missing: that route is simply
            # unavailable here, which is a reason to try the next one.
            self._clients[route.key] = self.factory(route, self.ledger, self.retry, self.settings)
        return self._clients[route.key]

    def _available(self, route: Route, reasons: list[str]) -> Any | None:
        try:
            client = self._client(route)
        except ValueError as exc:
            reasons.append(f"{route.key}: unavailable ({exc})")
            return None
        exhausted = self.pacer.exhausted_for(route.model)
        if exhausted:
            reasons.append(f"{route.key}: daily quota exhausted for {exhausted:.0f}s more")
            return None
        return client

    def _note_failure(self, route: Route, exc: Exception, reasons: list[str]) -> None:
        reasons.append(f"{route.key}: {type(exc).__name__}: {str(exc)[:300]}")
        reset = daily_quota_reset_s(exc)
        if reset is not None:
            self.pacer.mark_exhausted(route.model, reset)

    def complete_structured(
        self,
        messages: str | list[dict[str, Any]],
        schema: type[TModel],
        *,
        system: str | None = None,
        label: str | None = None,
    ) -> TModel:
        reasons: list[str] = []
        estimate: int | None = None
        for route in self.tier.routes:
            client = self._available(route, reasons)
            if client is None:
                continue
            overrides: dict[str, Any] = {}
            if route.reasoning_effort:
                overrides["reasoning_effort"] = route.reasoning_effort
            for attempt in range(1 + route.schema_retries):
                if route.tpm_limit:
                    estimate = estimate or (
                        estimate_tokens((system or "") + str(messages)) + EXPECTED_OUTPUT_TOKENS
                    )
                    self.pacer.wait(route.model, route.tpm_limit, estimate)
                before = len(self.ledger.records)
                try:
                    result = client.complete_structured(
                        messages, schema, system=system, label=label or self.name, **overrides
                    )
                except LLMBudgetError:
                    raise
                except (LLMTransientError, LLMPermanentError, LLMOutputError) as exc:
                    self._note_failure(route, exc, reasons)
                    if is_schema_rejection(exc) and attempt < route.schema_retries:
                        continue
                    break
                finally:
                    used = sum(r.usage.total_tokens for r in self.ledger.records[before:])
                    self.pacer.record(route.model, used)
                self.served_by.append(route.model)
                if reasons:
                    self.fallback_reasons.extend(reasons)
                return result
        self.fallback_reasons.extend(reasons)
        raise LLMRouteError(self.name, reasons)

    def call_tools(
        self,
        messages: str | list[dict[str, Any]],
        tools: Sequence[Tool],
        *,
        system: str | None = None,
        label: str | None = None,
        max_iterations: int = 6,
        deadline_s: float = 120.0,
        on_step: Callable[[int, Any, list[Any]], None] | None = None,
    ) -> AgentResult:
        """The tool loop on the first available route; on a provider failure the loop is
        restarted on the next route with what is left of the deadline and iterations.

        Restarting is safe because every tool in this project is idempotent against the
        store (a second `draft_reply` for the same review is refused as a duplicate), and
        the ledger is shared, so the budget covers both attempts."""
        reasons: list[str] = []
        started = time.monotonic()
        used_iterations = 0
        for route in self.tier.routes:
            client = self._available(route, reasons)
            if client is None:
                continue
            remaining_s = deadline_s - (time.monotonic() - started)
            remaining_iterations = max_iterations - used_iterations
            if remaining_s <= 0 or remaining_iterations <= 0:
                break
            overrides: dict[str, Any] = {}
            if route.reasoning_effort:
                overrides["reasoning_effort"] = route.reasoning_effort
            steps_seen = 0

            def counting_step(
                iteration: int, message: Any, outcomes: list[Any], offset: int = used_iterations
            ) -> None:
                nonlocal steps_seen
                steps_seen = iteration
                if on_step is not None:
                    on_step(offset + iteration, message, outcomes)

            try:
                result = client.call_tools(
                    messages,
                    tools,
                    system=system,
                    label=label or self.name,
                    max_iterations=remaining_iterations,
                    deadline_s=remaining_s,
                    on_step=counting_step,
                    **overrides,
                )
            except LLMBudgetError:
                raise
            except (LLMTransientError, LLMPermanentError, LLMOutputError) as exc:
                self._note_failure(route, exc, reasons)
                used_iterations += steps_seen
                continue
            self.served_by.append(route.model)
            if reasons:
                self.fallback_reasons.extend(reasons)
            result.iterations += used_iterations
            return result
        self.fallback_reasons.extend(reasons)
        raise LLMRouteError(self.name, reasons)
