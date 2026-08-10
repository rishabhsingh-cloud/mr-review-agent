"""Write immutable events to the agent_events hypertable (Time lane).

Every LLM call and each review's outcome is recorded so the trace viewer, audit
trail, and cost ledger can be reconstructed from one source.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.aggregator import GateDecision
from app.agents.base import AgentResult
from app.db.models import AgentEvent


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]


async def record_event(session: AsyncSession, **fields) -> AgentEvent:
    event = AgentEvent(span_id=new_span_id(), **fields)
    session.add(event)
    await session.flush()
    return event


async def record_agent_calls(
    session: AsyncSession,
    review_id: int,
    trace_id: str,
    agent_results: list[AgentResult],
) -> None:
    """One 'llm_call' event per specialist run."""
    for r in agent_results:
        resp = r.response
        await record_event(
            session,
            trace_id=trace_id,
            review_id=review_id,
            event_type="llm_call",
            agent_type=r.agent_type,
            provider=resp.provider if resp else None,
            model=resp.model if resp else None,
            tokens_in=resp.input_tokens if resp else None,
            tokens_out=resp.output_tokens if resp else None,
            cost_usd=resp.cost_usd if resp else 0,
            latency_ms=r.latency_ms,
            outcome="error" if r.error else "ok",
            event_metadata={
                "findings": len(r.findings),
                "error": r.error,
                "file": getattr(r, "target", None),
            },
        )


async def record_review_event(
    session: AsyncSession,
    review_id: int,
    trace_id: str,
    decision: GateDecision,
    cost_usd: float,
) -> None:
    await record_event(
        session,
        trace_id=trace_id,
        review_id=review_id,
        event_type="review",
        cost_usd=cost_usd,
        outcome=decision.action,
        event_metadata={
            "total": decision.total,
            "critical": decision.critical,
            "low_confidence": decision.low_confidence,
            "reason": decision.reason,
        },
    )
