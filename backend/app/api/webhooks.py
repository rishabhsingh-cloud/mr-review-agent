"""GitLab webhook ingress.

Flow: validate X-Gitlab-Token -> parse the /review command -> allowlist check
-> idempotency (project:mr:note) -> enqueue a review job. Returns fast; the
worker does the heavy lifting.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import WebhookDelivery
from app.db.session import get_session
from app.queue.jobs import enqueue
from app.webhooks.parser import (
    parse_note_event,
    parse_push_event,
    verify_webhook_token,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhooks/gitlab")
async def gitlab_webhook(
    request: Request,
    x_gitlab_token: str | None = Header(default=None),
    x_gitlab_event: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    settings = get_settings()

    if not verify_webhook_token(x_gitlab_token, settings.webhook_secret):
        raise HTTPException(status_code=401, detail="invalid token")

    payload = await request.json()

    # Push to the default branch → incremental RAG re-index.
    push = parse_push_event(payload)
    if push is not None:
        delivery_key = f"push:{push.project_id}:{push.after_sha}"
        session.add(WebhookDelivery(delivery_key=delivery_key, event_type="push"))
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            return {"status": "duplicate"}
        job_id = await enqueue(
            session,
            job_type="reindex_push",
            payload={
                "project_id": push.project_id,
                "after_sha": push.after_sha,
                "changed": push.changed,
                "removed": push.removed,
            },
        )
        await session.commit()
        logger.info(
            "Queued reindex job %s for project %s (%d changed, %d removed)",
            job_id, push.project_id, len(push.changed), len(push.removed),
        )
        return {"status": "reindex_queued", "job_id": job_id}

    req = parse_note_event(payload, settings.review_command)
    if req is None:
        # Not a /review comment or a default-branch push — nothing to do.
        return {"status": "ignored"}

    if not settings.is_reviewer_allowed(req.author_username):
        logger.info("Ignoring /review from non-allowlisted user %r", req.author_username)
        return {"status": "unauthorized"}

    delivery_key = f"{req.project_id}:{req.mr_iid}:{req.note_id}"
    session.add(WebhookDelivery(delivery_key=delivery_key, event_type="note"))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return {"status": "duplicate"}

    job_id = await enqueue(
        session,
        job_type="review_mr",
        payload={
            "project_id": req.project_id,
            "mr_iid": req.mr_iid,
            "note_id": req.note_id,
            "scope": req.command_scope,
            "triggered_by": req.author_username,
            "head_sha": req.head_sha,
        },
    )
    await session.commit()
    logger.info(
        "Queued review job %s for project %s MR !%s (scope=%s)",
        job_id, req.project_id, req.mr_iid, req.command_scope,
    )
    return {"status": "queued", "job_id": job_id, "scope": req.command_scope}
