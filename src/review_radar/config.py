"""Runtime configuration, read once from the environment or `.env`.

Every secret is a `SecretStr`, so logging the settings object (or an exception that renders
it) prints `**********` instead of a key. A missing key degrades a feature rather than
crashing import: no Groq key -> the router falls back to Gemini, no DATABASE_URL -> the
in-memory store, no APP_ENCRYPTION_KEY -> per-app GitHub tokens cannot be stored (the
`GITHUB_TOKEN` env fallback still works), no OPERATOR_TOKEN -> every write route is closed.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from llm_kit import Settings as LLMSettings
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- storage ------------------------------------------------------------------
    database_url: SecretStr | None = None
    # Direct (unpooled) Neon URL, used only by `uv run migrate` when set.
    database_direct_url: SecretStr | None = None

    # --- model providers (handed to llm-kit explicitly, see llm_settings) ---------
    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434/v1"

    # --- embeddings -----------------------------------------------------------------
    # "gemini" (gemini-embedding-001; any 128..3072 dims), "voyage" (voyage-4-lite; 256, 512,
    # 1024 or 2048 dims) or "hash" (deterministic, offline, for tests and keyless local
    # runs; useless for real similarity). See docs/decisions/0005-voyage-embeddings.md.
    embedding_provider: Literal["gemini", "voyage", "hash"] = "gemini"
    voyage_api_key: SecretStr | None = None
    # The dimension of reviews.embedding / themes.embedding. A deployment choice: `uv run
    # migrate` renders it into the migrations, and the embedder is asked for exactly this
    # many dimensions, so the two always agree. 768 for Gemini, 1024 for Voyage.
    embedding_dimensions: int = Field(default=768, ge=64, le=4096)

    @field_validator("embedding_provider", mode="before")
    @classmethod
    def _lower_provider(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    # --- secrets for writes ------------------------------------------------------------
    # Bearer token for every write route (single operator, no accounts in the MVP).
    operator_token: SecretStr | None = None
    # 32+ random bytes, base64. Per-app GitHub tokens are encrypted with a key derived from
    # it (HKDF per app id, AES-256-GCM). See crypto.py.
    app_encryption_key: SecretStr | None = None
    # Fallback GitHub token for issue creation when an app has no token of its own.
    github_token: SecretStr | None = None

    # --- API ----------------------------------------------------------------------
    cron_secret: SecretStr | None = None
    web_origin: str = "http://localhost:3000,http://localhost:3600"
    port: int = 7860
    runs_per_hour_per_ip: int = 20
    trust_proxy_headers: bool = False
    ip_hash_salt: SecretStr = SecretStr("review-radar")

    # --- run dispatch (see dispatch.py) ---------------------------------------------
    vercel: bool = False
    public_api_url: str | None = None
    qstash_url: str = "https://qstash.upstash.io"
    qstash_token: SecretStr | None = None
    qstash_current_signing_key: SecretStr | None = None
    qstash_next_signing_key: SecretStr | None = None
    process_time_budget_s: float = 240.0

    # --- agent run ------------------------------------------------------------------
    max_reviews_per_run: int = Field(default=50, ge=1, le=500)
    max_usd_per_run: float | None = None  # None -> routing.toml budget
    feed_max_pages: int = Field(default=10, ge=1, le=10)

    # --- observability (optional) ---------------------------------------------------
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    log_level: str = "INFO"

    @property
    def web_origins(self) -> list[str]:
        return [origin.strip() for origin in self.web_origin.split(",") if origin.strip()]

    def llm_settings(self) -> LLMSettings:
        """llm-kit settings built from *this* project's config, so this repository's `.env`
        (or the deploy environment) is the only source of keys."""
        return LLMSettings(
            gemini_api_key=self.gemini_api_key,
            groq_api_key=self.groq_api_key,
            openrouter_api_key=self.openrouter_api_key,
            ollama_base_url=self.ollama_base_url,
        )

    def has_key(self, provider: str) -> bool:
        secret = getattr(self, f"{provider}_api_key", None)
        return bool(secret and secret.get_secret_value())

    @staticmethod
    def secret(value: SecretStr | None) -> str:
        return value.get_secret_value() if value is not None else ""


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
