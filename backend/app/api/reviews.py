"""Read endpoints: reviews list, review detail, and per-review event trace."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentEvent, FindingRecord, MrReviewRecord
from app.db.session import get_session

router = APIRouter()


def _review_summary(r: MrReviewRecord) -> dict:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "mr_iid": r.mr_iid,
        "scope": r.command_scope,
        "status": r.status,
        "triggered_by": r.triggered_by,
        "cost_usd": float(r.total_cost_usd),
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _finding_dict(f: FindingRecord) -> dict:
    return {
        "id": f.id,
        "agent_type": f.agent_type,
        "severity": f.severity,
        "category": f.category,
        "title": f.title,
        "file_path": f.file_path,
        "line_start": f.line_start,
        "line_end": f.line_end,
        "confidence": f.confidence,
        "rationale": f.rationale,
        "status": f.status,
    }


def _event_dict(e: AgentEvent) -> dict:
    return {
        "ts": e.ts.isoformat() if e.ts else None,
        "event_type": e.event_type,
        "agent_type": e.agent_type,
        "model": e.model,
        "tokens_in": e.tokens_in,
        "tokens_out": e.tokens_out,
        "cost_usd": float(e.cost_usd) if e.cost_usd is not None else None,
        "latency_ms": e.latency_ms,
        "outcome": e.outcome,
    }


@router.get("/reviews")
async def list_reviews(limit: int = 50, session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (
        await session.execute(
            select(MrReviewRecord).order_by(desc(MrReviewRecord.id)).limit(limit)
        )
    ).scalars().all()
    return [_review_summary(r) for r in rows]


@router.get("/reviews/{review_id}")
async def get_review(review_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    review = await session.get(MrReviewRecord, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    findings = (
        await session.execute(
            select(FindingRecord).where(FindingRecord.review_id == review_id)
        )
    ).scalars().all()
    return {**_review_summary(review), "summary": review.summary,
            "findings": [_finding_dict(f) for f in findings]}


@router.get("/reviews/{review_id}/events")
async def get_review_events(
    review_id: int, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    events = (
        await session.execute(
            select(AgentEvent).where(AgentEvent.review_id == review_id).order_by(AgentEvent.ts)
        )
    ).scalars().all()
    return [_event_dict(e) for e in events]
