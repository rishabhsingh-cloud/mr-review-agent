"""Human-in-the-loop endpoints: pending queue, approve, reject, dispute."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.reviews import _finding_dict, _review_summary
from app.db.models import FindingRecord, HitlReview, MrReviewRecord
from app.db.session import get_session
from app.orchestrator.hitl_service import (
    approve_and_publish,
    dispute_finding,
    reject_review,
)

router = APIRouter()


class DecisionBody(BaseModel):
    decided_by: str | None = None
    reason: str | None = None


class DisputeBody(BaseModel):
    comment: str | None = None
    created_by: str | None = None


@router.get("/hitl/pending")
async def pending(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Reviews awaiting a human decision, with their findings."""
    rows = (
        await session.execute(
            select(MrReviewRecord)
            .join(HitlReview, HitlReview.review_id == MrReviewRecord.id)
            .where(HitlReview.status == "pending")
            .order_by(desc(MrReviewRecord.id))
        )
    ).scalars().all()

    result = []
    for r in rows:
        findings = (
            await session.execute(
                select(FindingRecord).where(FindingRecord.review_id == r.id)
            )
        ).scalars().all()
        result.append({**_review_summary(r), "findings": [_finding_dict(f) for f in findings]})
    return result


@router.post("/hitl/{review_id}/approve")
async def approve(
    review_id: int, body: DecisionBody, session: AsyncSession = Depends(get_session)
) -> dict:
    try:
        result = await approve_and_publish(session, review_id, body.decided_by)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return {"status": "approved", **result}


@router.post("/hitl/{review_id}/reject")
async def reject(
    review_id: int, body: DecisionBody, session: AsyncSession = Depends(get_session)
) -> dict:
    try:
        await reject_review(session, review_id, body.decided_by, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return {"status": "rejected"}


@router.post("/findings/{finding_id}/dispute")
async def dispute(
    finding_id: int, body: DisputeBody, session: AsyncSession = Depends(get_session)
) -> dict:
    try:
        await dispute_finding(session, finding_id, body.comment, body.created_by)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return {"status": "recorded"}
