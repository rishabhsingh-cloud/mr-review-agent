"""Tests for specialist runner, aggregation/gating, and the LangGraph pipeline.

A fake LLMProvider returns canned structured findings so the whole graph runs
without any network or API key.
"""

from __future__ import annotations

from typing import Any

from app.agents.aggregator import (
    AUTO_POST,
    ESCALATE,
    HITL,
    aggregate,
    decide_gate,
)
from app.agents.base import AgentResult
from app.agents.schemas import Finding, Severity
from app.agents.specialists import agents_for_scope
from app.llm.provider import LLMProvider, LLMResponse, StructuredResult
from app.orchestrator.graph import run_review


# ── scope selection ──


def test_scope_all_returns_four():
    assert len(agents_for_scope("all")) == 4


def test_scope_specific_returns_one():
    agents = agents_for_scope("security")
    assert len(agents) == 1 and agents[0].agent_type == "security"


# ── aggregation ──


def test_aggregate_dedups_same_line_keeps_highest_confidence():
    a = AgentResult("security", [Finding(title="SQLi", file_path="x.py", line_start=10, confidence=0.7)])
    b = AgentResult("quality", [Finding(title="sqli", file_path="x.py", line_start=10, confidence=0.9)])
    agg = aggregate([a, b])
    assert len(agg) == 1
    assert agg[0].finding.confidence == 0.9
    assert set(agg[0].agents) == {"security", "quality"}


# ── gate ──


def test_gate_escalates_on_critical():
    agg = aggregate([AgentResult("security", [
        Finding(title="rce", severity=Severity.critical, confidence=0.95)
    ])])
    assert decide_gate(agg, min_confidence=0.6).action == ESCALATE


def test_gate_hitl_on_low_confidence():
    agg = aggregate([AgentResult("quality", [
        Finding(title="maybe bug", severity=Severity.medium, confidence=0.4)
    ])])
    assert decide_gate(agg, min_confidence=0.6).action == HITL


def test_gate_auto_posts_when_all_confident():
    agg = aggregate([AgentResult("quality", [
        Finding(title="clear issue", severity=Severity.medium, confidence=0.85)
    ])])
    assert decide_gate(agg, min_confidence=0.6).action == AUTO_POST


# ── full graph with a fake provider ──


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, findings: list[dict]):
        self._findings = findings

    async def complete(self, **kwargs: Any) -> LLMResponse:  # pragma: no cover
        return LLMResponse(text="", model="fake", provider="fake")

    async def complete_structured(self, **kwargs: Any) -> StructuredResult:
        return StructuredResult(
            data={"findings": self._findings},
            response=LLMResponse(
                text="", model=kwargs.get("model", "fake"), provider="fake",
                input_tokens=5, output_tokens=5, cost_usd=0.001,
            ),
        )


async def _empty_retriever(_diff: str) -> str:
    return ""


async def test_graph_runs_end_to_end():
    provider = FakeProvider(
        findings=[{
            "title": "Hardcoded secret",
            "file_path": "app/config.py",
            "line_start": 12,
            "severity": "critical",
            "confidence": 0.95,
            "rationale": "API key committed",
        }]
    )
    files = [{"path": "app/config.py", "diff_text": "+ API_KEY = 'abc'"}]
    state = await run_review(provider, _empty_retriever, files=files, scope="security")
    assert len(state["aggregated"]) == 1
    # security scope → one agent → one finding; critical → escalate.
    assert state["decision"].action == ESCALATE
    assert state["decision"].critical == 1


async def test_graph_full_panel_dedups_across_agents():
    # Same finding from all 4 agents collapses to one.
    provider = FakeProvider(
        findings=[{
            "title": "Missing null check",
            "file_path": "app/x.py",
            "line_start": 5,
            "severity": "medium",
            "confidence": 0.8,
        }]
    )
    files = [{"path": "app/x.py", "diff_text": "..."}]
    state = await run_review(provider, _empty_retriever, files=files, scope="all")
    assert len(state["aggregated"]) == 1
    assert len(state["aggregated"][0].agents) == 4
    assert state["decision"].action == AUTO_POST
