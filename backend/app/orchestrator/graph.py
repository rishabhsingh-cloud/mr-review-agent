"""Build the review graph: retrieve per-file context -> fan out specialists
PER FILE -> aggregate + gate.

Reviewing each changed file separately (rather than one giant multi-file prompt)
gives every file the model's full attention, which matters on large MRs. A
semaphore caps concurrent agent calls. Dependencies (LLM provider, context
retriever) are injected so the graph can be unit-tested with fakes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.aggregator import GateDecision, aggregate, decide_gate
from app.agents.base import AgentResult, run_agent
from app.agents.specialists import agents_for_scope
from app.config import get_settings
from app.llm.factory import model_for_agent
from app.llm.provider import LLMProvider

# retriever(diff_text) -> context_text
Retriever = Callable[[str], Awaitable[str]]


class ReviewState(TypedDict, total=False):
    files: list[dict]          # [{"path": str, "diff_text": str}]
    scope: str
    contexts: dict[str, str]   # {path: retrieved context}
    agent_results: list[AgentResult]
    aggregated: list[Any]
    decision: GateDecision


def build_review_graph(provider: LLMProvider, retriever: Retriever):
    settings = get_settings()
    concurrency = max(1, settings.review_concurrency)

    async def retrieve_node(state: ReviewState) -> dict:
        files = state.get("files", [])
        sem = asyncio.Semaphore(concurrency)

        async def one(f: dict) -> tuple[str, str]:
            async with sem:
                return f["path"], await retriever(f["diff_text"])

        pairs = await asyncio.gather(*[one(f) for f in files])
        return {"contexts": dict(pairs)}

    async def fanout_node(state: ReviewState) -> dict:
        files = state.get("files", [])
        contexts = state.get("contexts", {})
        specialists = agents_for_scope(state.get("scope", "all"))
        sem = asyncio.Semaphore(concurrency)

        async def one(file_unit: dict, specialist) -> AgentResult:
            async with sem:
                result = await run_agent(
                    provider,
                    specialist,
                    diff_text=file_unit["diff_text"],
                    context_text=contexts.get(file_unit["path"], ""),
                    model=model_for_agent(specialist.agent_type),
                    max_tokens=settings.llm_max_tokens,
                    temperature=settings.llm_temperature,
                )
            result.target = file_unit["path"]
            # Backfill file path for findings that omitted it (we know the file).
            for finding in result.findings:
                if not finding.file_path:
                    finding.file_path = file_unit["path"]
            return result

        tasks = [one(f, s) for f in files for s in specialists]
        results = await asyncio.gather(*tasks)
        return {"agent_results": list(results)}

    async def aggregate_node(state: ReviewState) -> dict:
        aggregated = aggregate(state.get("agent_results", []))
        decision = decide_gate(aggregated, min_confidence=settings.confidence_threshold)
        return {"aggregated": aggregated, "decision": decision}

    graph = StateGraph(ReviewState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("fanout", fanout_node)
    graph.add_node("aggregate", aggregate_node)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "fanout")
    graph.add_edge("fanout", "aggregate")
    graph.add_edge("aggregate", END)
    return graph.compile()


async def run_review(
    provider: LLMProvider,
    retriever: Retriever,
    *,
    files: list[dict],
    scope: str = "all",
) -> ReviewState:
    """Build + invoke the graph over per-file units; return the final state."""
    compiled = build_review_graph(provider, retriever)
    return await compiled.ainvoke({"files": files, "scope": scope})
