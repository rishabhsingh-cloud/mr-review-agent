"""Persist review records, findings, and HITL queue entries (Truth lane)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.aggregator import (
    AUTO_POST,
    ESCALATE,
    HITL,
    AggregatedFinding,
    GateDecision,
)
from app.db.models import FindingRecord, HitlReview, MrReviewRecord

# Gate action -> review record status.
_STATUS_BY_ACTION = {
    AUTO_POST: "completed",
    HITL: "pending_review",
    ESCALATE: "escalated",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_review(
    session: AsyncSession,
    *,
    repository_id: int | None,
    project_id: int,
    mr_iid: int,
    head_sha: str,
    scope: str,
    triggered_by: str | None,
    trace_id: str | None = None,
) -> MrReviewRecord:
    review = MrReviewRecord(
        repository_id=repository_id,
        project_id=project_id,
        mr_iid=mr_iid,
        head_sha=head_sha or "",
        command_scope=scope,
        triggered_by=triggered_by,
        status="running",
        trace_id=trace_id,
        started_at=_now(),
    )
    session.add(review)
    await session.flush()
    return review


async def finalize_review(
    session: AsyncSession,
    review: MrReviewRecord,
    decision: GateDecision,
    aggregated: list[AggregatedFinding],
    *,
    cost_usd: float,
) -> None:
    review.status = _STATUS_BY_ACTION.get(decision.action, "completed")
    review.total_cost_usd = round(cost_usd, 6)
    review.summary = decision.reason
    review.completed_at = _now()

    posted = decision.action == AUTO_POST
    for agg in aggregated:
        f = agg.finding
        session.add(
            FindingRecord(
                review_id=review.id,
                agent_type=",".join(agg.agents),
                severity=f.severity.value,
                category=f.category,
                title=f.title,
                file_path=f.file_path,
                line_start=f.line_start,
                line_end=f.line_end,
                confidence=f.confidence,
                rationale=f.rationale,
                dedup_key=f"{f.file_path or ''}:{f.line_start or 0}",
                status="posted" if posted else "held",
            )
        )

    if decision.action in (HITL, ESCALATE):
        session.add(
            HitlReview(review_id=review.id, status="pending", reason=decision.reason)
        )

    await session.flush()
