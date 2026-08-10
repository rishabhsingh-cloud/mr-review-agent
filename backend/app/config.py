"""Central configuration, loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Repo root = backend/app/config.py -> parents[2]. Keeps .env discoverable no
# matter the working directory (app runs from repo root, Alembic from backend/).
_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://mragent:mragent@localhost:5432/mragent"
    )

    # --- GitLab ---
    gitlab_url: str = Field(default="https://gitlab.example.com")
    gitlab_token: str = Field(default="")
    webhook_secret: str = Field(default="")
    gitlab_ssl_verify: bool = Field(default=True)  # set False (or a CA path) for internal certs
    gitlab_timeout: int = Field(default=30)

    # --- Trigger / authorization ---
    review_command: str = Field(default="/review")
    # NoDecode: keep pydantic-settings from JSON-parsing this; our validator
    # splits the comma-separated string instead.
    reviewer_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- LLM providers ---
    # Project uses OpenAI for both the review LLM and embeddings.
    llm_provider: str = Field(default="openai_compat")  # openai_compat | anthropic
    anthropic_api_key: str = Field(default="")
    openai_api_key: str = Field(default="")
    openai_compat_base_url: str = Field(default="")  # blank = OpenAI default endpoint
    openai_compat_api_key: str = Field(default="")  # blank = fall back to openai_api_key

    # Model routing. `review_model` = deep passes, `fast_model` = cheap passes.
    review_model: str = Field(default="gpt-5.5")
    fast_model: str = Field(default="gpt-5-mini")
    # Generous cap: reasoning models spend hidden reasoning tokens against this,
    # so a low value can starve the actual JSON output.
    llm_max_tokens: int = Field(default=16000)
    llm_temperature: float = Field(default=0.0)  # ignored for reasoning models

    # --- Embeddings ---
    embedding_model: str = Field(default="text-embedding-3-large")
    embedding_dim: int = Field(default=256)

    # --- RAG ---
    rag_chunk_max_tokens: int = Field(default=400)
    rag_overlap_lines: int = Field(default=8)
    rag_embed_batch: int = Field(default=96)
    rag_top_k: int = Field(default=8)
    rag_candidate_pool: int = Field(default=24)

    # --- Review fan-out ---
    review_concurrency: int = Field(default=8)  # max concurrent agent calls
    review_max_file_diff_chars: int = Field(default=40000)  # per-file diff cap

    # --- Aggregation / gating ---
    confidence_threshold: float = Field(default=0.6)  # below this → human review

    # --- Reliability ---
    agent_timeout_seconds: int = Field(default=120)
    llm_retry_attempts: int = Field(default=3)

    # --- Cost control ---
    daily_budget_usd: float = Field(default=25.0)

    # --- App ---
    log_level: str = Field(default="INFO")

    @field_validator("reviewer_allowlist", mode="before")
    @classmethod
    def _split_allowlist(cls, v: object) -> object:
        """Accept a comma-separated string from env and normalize to a list."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    def is_reviewer_allowed(self, username: str | None) -> bool:
        """True if the given GitLab username may trigger a review.

        A `*` entry in the allowlist permits any (non-empty) user.
        """
        if not username:
            return False
        if "*" in self.reviewer_allowlist:
            return True
        return username in self.reviewer_allowlist


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so config is parsed once per process."""
    return Settings()
