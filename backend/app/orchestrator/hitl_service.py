"""HITL actions: publish an approved review's held findings, reject, dispute."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FindingRecord, HitlFeedback, HitlReview, MrReviewRecord
from app.gitlab.client import get_merge_request, get_mr_changes
from app.gitlab.diff import parse_mr_changes
from app.gitlab.publish import GitLabPublisher, InlineComment
from app.orchestrator.render import format_finding_record

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _load_findings(session: AsyncSession, review_id: int) -> list[FindingRecord]:
    return list(
        (
            await session.execute(
                select(FindingRecord).where(FindingRecord.review_id == review_id)
            )
        ).scalars()
    )


async def _get_hitl(session: AsyncSession, review_id: int) -> HitlReview | None:
    return (
        await session.execute(
            select(HitlReview).where(HitlReview.review_id == review_id)
        )
    ).scalar_one_or_none()


async def approve_and_publish(
    session: AsyncSession, review_id: int, decided_by: str | None
) -> dict:
    """Publish the review's held findings to the MR and mark it approved."""
    review = await session.get(MrReviewRecord, review_id)
    if review is None:
        raise ValueError(f"review {review_id} not found")

    findings = await _load_findings(session, review_id)

    # Re-fetch the MR to rebuild diff positions (synchronous client → thread).
    mr = await asyncio.to_thread(get_merge_request, review.project_id, review.mr_iid)
    changes = await asyncio.to_thread(get_mr_changes, mr)
    refs, file_diffs = parse_mr_changes(changes)

    comments = [
        InlineComment(f.file_path, f.line_start, format_finding_record(f))
        for f in findings
        if f.file_path and f.line_start
    ]
    summary = f"## 🤖 AI Review — checked and approved by {decided_by or 'a reviewer'}"
    # Post as whoever ran /review, same as an auto-posted review.
    publisher = GitLabPublisher(mr, refs, file_diffs, post_as=review.triggered_by)
    result = await asyncio.to_thread(publisher.publish, comments, summary)

    for f in findings:
        f.status = "posted"
    review.status = "completed"
    hitl = await _get_hitl(session, review_id)
    if hitl:
        hitl.status = "approved"
        hitl.decided_by = decided_by
        hitl.decided_at = _now()
    await session.flush()
    logger.info("Approved review %s: posted %d inline", review_id, result.inline_posted)
    return {"inline_posted": result.inline_posted, "fell_back": result.fell_back}


async def reject_review(
    session: AsyncSession, review_id: int, decided_by: str | None, reason: str | None
) -> None:
    review = await session.get(MrReviewRecord, review_id)
    if review is None:
        raise ValueError(f"review {review_id} not found")
    review.status = "rejected"
    for f in await _load_findings(session, review_id):
        f.status = "dismissed"
    hitl = await _get_hitl(session, review_id)
    if hitl:
        hitl.status = "rejected"
        hitl.decided_by = decided_by
        hitl.decided_at = _now()
        if reason:
            hitl.reason = reason
    await session.flush()


async def dispute_finding(
    session: AsyncSession, finding_id: int, comment: str | None, created_by: str | None
) -> None:
    finding = await session.get(FindingRecord, finding_id)
    if finding is None:
        raise ValueError(f"finding {finding_id} not found")
    session.add(
        HitlFeedback(
            finding_id=finding_id,
            review_id=finding.review_id,
            feedback_type="dispute",
            comment=comment,
            created_by=created_by,
        )
    )
    await session.flush()
