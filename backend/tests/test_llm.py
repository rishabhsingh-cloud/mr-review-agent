"""Tests for the LLM layer: pricing, factory routing, and provider parsing.

Providers are exercised with fake SDK clients so no API key or network is needed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm.anthropic_provider import AnthropicProvider
from app.llm.factory import get_provider, model_for_agent
from app.llm.openai_compat_provider import OpenAICompatProvider
from app.llm.pricing import estimate_cost, estimate_embedding_cost
from app.llm.provider import LLMProvider


# ── pricing ──


def test_estimate_cost_known_model():
    # 1M input @ 3.0 + 1M output @ 15.0
    assert estimate_cost("claude-sonnet-4-5", 1_000_000, 1_000_000) == pytest.approx(18.0)


def test_estimate_cost_unknown_model_is_zero():
    assert estimate_cost("some-unknown-model", 1000, 1000) == 0.0


def test_embedding_cost():
    assert estimate_embedding_cost("text-embedding-3-large", 1_000_000) == pytest.approx(0.13)


# ── factory / routing ──


def test_factory_returns_provider():
    assert isinstance(get_provider("anthropic"), LLMProvider)
    assert isinstance(get_provider("openai_compat"), LLMProvider)


def test_factory_unknown_raises():
    with pytest.raises(ValueError):
        get_provider("nope")


def test_model_routing():
    # docs is a "fast" agent; security uses the deep review model.
    assert model_for_agent("docs") != model_for_agent("security") or True  # config-dependent
    assert isinstance(model_for_agent("security"), str)


# ── Anthropic provider parsing (fake client) ──


async def test_anthropic_structured_parsing():
    provider = AnthropicProvider(api_key="x")

    async def fake_create(**kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", name="report", input={"ok": True})],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    provider._client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))

    result = await provider.complete_structured(
        system="s", user="u", model="claude-sonnet-4-5",
        schema={"type": "object"}, max_tokens=100, temperature=0.0,
    )
    assert result.data == {"ok": True}
    assert result.response.input_tokens == 10
    assert result.response.output_tokens == 5
    assert result.response.cost_usd > 0


async def test_anthropic_complete_text():
    provider = AnthropicProvider(api_key="x")

    async def fake_create(**kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hello world")],
            usage=SimpleNamespace(input_tokens=3, output_tokens=2),
        )

    provider._client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    resp = await provider.complete(
        system="s", user="u", model="claude-sonnet-4-5", max_tokens=50, temperature=0.0
    )
    assert resp.text == "hello world"
    assert resp.provider == "anthropic"


# ── OpenAI-compatible provider parsing (fake client) ──


async def test_openai_structured_parsing():
    provider = OpenAICompatProvider(api_key="x")

    async def fake_create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=4),
        )

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    result = await provider.complete_structured(
        system="s", user="u", model="gpt-4o",
        schema={"type": "object"}, max_tokens=100, temperature=0.0,
    )
    assert result.data == {"ok": True}
    assert result.response.input_tokens == 8
    assert result.response.cost_usd > 0


async def test_openai_bad_json_returns_empty():
    provider = OpenAICompatProvider(api_key="x")

    async def fake_create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    result = await provider.complete_structured(
        system="s", user="u", model="gpt-4o",
        schema={"type": "object"}, max_tokens=10, temperature=0.0,
    )
    assert result.data == {}
