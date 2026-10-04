import ast
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from llm_kit import AgentResult, Ledger, LLMPermanentError, LLMTransientError, RetryPolicy
from pydantic import BaseModel

from review_radar.crypto import SecretboxError, decrypt_secret, encrypt_secret, mask_token
from review_radar.embeddings import HashEmbedder, dot
from review_radar.llm import (
    LLMRouteError,
    RoutedLLM,
    TokenPacer,
    _SignatureShim,
    daily_quota_reset_s,
)
from review_radar.routing import Route, Tier, load_routing

from .conftest import ENCRYPTION_KEY

# ------------------------------------------------------------------ crypto


def test_round_trip_and_binding_to_app():
    sealed = encrypt_secret(ENCRYPTION_KEY, "app-1", "github_pat_secret_value")
    assert sealed.startswith("v1.") and "secret" not in sealed
    assert decrypt_secret(ENCRYPTION_KEY, "app-1", sealed) == "github_pat_secret_value"
    assert encrypt_secret(ENCRYPTION_KEY, "app-1", "x") != encrypt_secret(
        ENCRYPTION_KEY, "app-1", "x"
    )
    with pytest.raises(SecretboxError):
        decrypt_secret(ENCRYPTION_KEY, "app-2", sealed)  # copied to another app's row


def test_tamper_wrong_key_and_bad_formats():
    sealed = encrypt_secret(ENCRYPTION_KEY, "a", "token")
    tampered = sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1]
    with pytest.raises(SecretboxError):
        decrypt_secret(ENCRYPTION_KEY, "a", tampered)
    other_key = base64.b64encode(b"k" * 32).decode()
    with pytest.raises(SecretboxError):
        decrypt_secret(other_key, "a", sealed)
    for bad in ("v2.abc", "nope", "v1.", "v1.AAAA"):
        with pytest.raises(SecretboxError):
            decrypt_secret(ENCRYPTION_KEY, "a", bad)


def test_master_key_validation():
    with pytest.raises(SecretboxError, match="not set"):
        encrypt_secret(None, "a", "x")
    with pytest.raises(SecretboxError, match="at least 32 bytes"):
        encrypt_secret(base64.b64encode(b"short").decode(), "a", "x")
    with pytest.raises(SecretboxError):
        encrypt_secret("not base64 !!", "a", "x")


def test_mask_token():
    assert mask_token("github_pat_11ABCDEFG0123456789wxyz") == "github_pat_…wxyz"
    assert mask_token("short") == "…"


# ------------------------------------------------------------------ embeddings


def test_hash_embedder_is_deterministic_unit_and_word_sensitive():
    embedder = HashEmbedder()
    a, b, c = embedder.embed(
        ["crashes on launch after update", "crashes on launch after the update", "love playlists"]
    )
    assert abs(dot(a, a) - 1) < 1e-9 and a == embedder.embed(["crashes on launch after update"])[0]
    assert dot(a, b) > 0.8 > dot(a, c)


# ------------------------------------------------------------------ routed LLM


class Out(BaseModel):
    value: str


class FakeClient:
    def __init__(self, route: Route, ledger: Ledger, behaviour):
        self.route = route
        self.ledger = ledger
        self.behaviour = behaviour
        self.calls = 0

    def complete_structured(self, messages, schema, **kwargs):
        self.calls += 1
        outcome = self.behaviour(self.route, self.calls)
        if isinstance(outcome, Exception):
            raise outcome
        return Out(value=outcome)

    def call_tools(self, messages, tools, **kwargs):
        self.calls += 1
        outcome = self.behaviour(self.route, self.calls)
        if isinstance(outcome, Exception):
            raise outcome
        if kwargs.get("on_step"):
            kwargs["on_step"](1, SimpleNamespace(content=outcome, tool_calls=[]), [])
        return AgentResult(outcome, [], [], 1, "final_answer")


def routed(behaviour, tier: Tier | None = None):
    clients: dict[str, FakeClient] = {}

    def factory(route, ledger, retry, settings):
        clients[route.key] = FakeClient(route, ledger, behaviour)
        return clients[route.key]

    tier = tier or load_routing().tier("extract")
    llm = RoutedLLM("extract", tier, ledger=Ledger(), retry=RetryPolicy(), factory=factory)
    llm.pacer = TokenPacer(sleep=lambda s: None)
    return llm, clients


DAILY = LLMTransientError(
    "429: Rate limit reached for model on tokens per day (TPD): Limit 200000. "
    "Please try again in 18m11.5s."
)


def test_fallback_on_daily_quota_then_route_is_parked():
    llm, clients = routed(lambda route, n: DAILY if route.provider == "groq" else "ok")
    assert llm.complete_structured("x", Out).value == "ok"
    assert llm.served_by == ["gemini-3.5-flash-lite"]
    assert llm.complete_structured("x", Out).value == "ok"
    assert clients["groq:openai/gpt-oss-20b"].calls == 1  # parked, not retried
    assert any("daily quota exhausted" in reason for reason in llm.fallback_reasons)


def test_schema_rejection_resamples_same_route_first():
    rejection = LLMPermanentError("400 json_validate_failed: does not match the expected schema")
    llm, clients = routed(lambda route, n: rejection if n == 1 else f"{route.provider}-{n}")
    assert llm.complete_structured("x", Out).value == "groq-2"
    assert "gemini:gemini-3.5-flash-lite" not in clients


def test_all_routes_failing_reports_quota():
    llm, _ = routed(lambda route, n: LLMTransientError("429 RESOURCE_EXHAUSTED quota"))
    with pytest.raises(LLMRouteError) as exc:
        llm.complete_structured("x", Out)
    assert exc.value.quota_exhausted and len(exc.value.reasons) == 2


def test_missing_key_route_is_skipped():
    def factory(route, ledger, retry, settings):
        if route.provider == "groq":
            raise ValueError("no API key for provider 'groq'")
        return FakeClient(route, ledger, lambda r, n: "gemini")

    llm = RoutedLLM("x", load_routing().tier("extract"), ledger=Ledger(), factory=factory)
    assert llm.complete_structured("x", Out).value == "gemini"
    assert "unavailable" in llm.fallback_reasons[0]


def test_call_tools_falls_back_and_offsets_iterations():
    steps = []
    llm, _ = routed(
        lambda route, n: (
            LLMTransientError("503 overloaded") if route.provider == "groq" else "done"
        ),
        tier=load_routing().tier("agent"),
    )
    result = llm.call_tools(
        "x", [], max_iterations=24, deadline_s=200, on_step=lambda *a: steps.append(a[0])
    )
    assert result.text == "done" and llm.served_by == ["gemini-3.5-flash-lite"]
    assert steps == [1]


def test_daily_quota_parser():
    assert daily_quota_reset_s(DAILY) == pytest.approx(18 * 60 + 11.5)
    assert daily_quota_reset_s(LLMTransientError("429 per minute")) is None
    assert daily_quota_reset_s(LLMTransientError("quota GenerateRequestsPerDay exceeded")) == 3600.0


def test_signature_shim_restores_gemini_thought_signatures():
    sent = []

    class Completions:
        def create(self, **kwargs):
            sent.append(kwargs)
            call = SimpleNamespace(
                id="call_1", model_extra={"extra_content": {"google": {"thought_signature": "sig"}}}
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    shim = _SignatureShim(client)
    shim.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    history = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "type": "function"}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
    ]
    shim.chat.completions.create(messages=history)
    restored = sent[1]["messages"][1]["tool_calls"][0]
    assert restored["extra_content"] == {"google": {"thought_signature": "sig"}}
    assert "extra_content" not in history[1]["tool_calls"][0]  # caller's list untouched


# ------------------------------------------------------------------ architecture


def test_agent_package_cannot_reach_executors():
    """The approval gate is structural: nothing under agent/ imports executors/."""
    root = Path(__file__).parents[1] / "src" / "review_radar" / "agent"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "executors" not in (node.module or ""), path.name
            if isinstance(node, ast.Import):
                assert all("executors" not in alias.name for alias in node.names), path.name
