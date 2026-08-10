"""Tests for telemetry id helpers and agent latency capture (no network/DB)."""

from __future__ import annotations

from typing import Any

from app.agents.base import run_agent
from app.agents.specialists import SPECIALISTS
from app.llm.provider import LLMProvider, LLMResponse, StructuredResult
from app.telemetry.events import new_span_id, new_trace_id


def test_trace_and_span_ids_unique():
    assert new_trace_id() != new_trace_id()
    assert len(new_span_id()) == 16


class _FakeProvider(LLMProvider):
    name = "fake"

    async def complete(self, **kwargs: Any) -> LLMResponse:  # pragma: no cover
        return LLMResponse(text="", model="m", provider="fake")

    async def complete_structured(self, **kwargs: Any) -> StructuredResult:
        return StructuredResult(
            data={"findings": []},
            response=LLMResponse(
                text="", model=kwargs["model"], provider="fake",
                input_tokens=1, output_tokens=1, cost_usd=0.0,
            ),
        )


async def test_run_agent_captures_latency():
    result = await run_agent(
        _FakeProvider(), SPECIALISTS["security"],
        diff_text="d", context_text="", model="m", max_tokens=10, temperature=0.0,
    )
    assert result.latency_ms >= 0
    assert result.error is None
    assert result.response is not None
