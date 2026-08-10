"""Provider selection and per-agent model routing."""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.openai_compat_provider import OpenAICompatProvider
from app.llm.provider import LLMProvider

# Which agents get the deep model vs the fast model. Adjust freely; unknown
# agent types fall back to the review (deep) model.
_FAST_AGENTS = {"docs"}


@lru_cache
def get_provider(name: str | None = None) -> LLMProvider:
    """Return the configured provider (cached per name)."""
    settings = get_settings()
    provider_name = name or settings.llm_provider
    if provider_name == "anthropic":
        return AnthropicProvider(api_key=settings.anthropic_api_key)
    if provider_name == "openai_compat":
        return OpenAICompatProvider(
            api_key=settings.openai_compat_api_key or settings.openai_api_key,
            base_url=settings.openai_compat_base_url or None,
        )
    raise ValueError(f"unknown llm_provider: {provider_name!r}")


def model_for_agent(agent_type: str) -> str:
    """Route an agent to a model id (deep vs fast pass)."""
    settings = get_settings()
    if agent_type in _FAST_AGENTS:
        return settings.fast_model
    return settings.review_model
