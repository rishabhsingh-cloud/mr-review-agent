"""Worker loop.

Claims jobs from the Postgres queue and dispatches them to a registered handler.
Each job runs in its own DB sessions so a handler failure never poisons the
claim/commit bookkeeping. The review handler is a placeholder for now — the
LangGraph orchestration is wired in a later bit.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.orchestrator.service import run_live_review
from app.queue.jobs import claim_next, mark_done, mark_failed

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0

Handler = Callable[[dict], Awaitable[None]]
HANDLERS: dict[str, Handler] = {}


def register(job_type: str) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn
    return decorator


@register("review_mr")
async def handle_review_mr(payload: dict) -> None:
    """Run a live review: fetch the MR from GitLab, review it, publish, persist."""
    logger.info(
        "Starting review: project=%s MR=!%s scope=%s by=%s",
        payload.get("project_id"), payload.get("mr_iid"),
        payload.get("scope"), payload.get("triggered_by"),
    )
    async with AsyncSessionLocal() as session:
        await run_live_review(
            session,
            project_id=int(payload["project_id"]),
            mr_iid=int(payload["mr_iid"]),
            scope=payload.get("scope", "all"),
            triggered_by=payload.get("triggered_by"),
        )
        await session.commit()


async def process_one(worker_id: str) -> bool:
    """Claim and run a single job. Returns False if the queue was empty."""
    async with AsyncSessionLocal() as session:
        job = await claim_next(session, worker_id)
        await session.commit()
        if job is None:
            return False
        job_id, job_type, payload = job.id, job.job_type, dict(job.payload)

    handler = HANDLERS.get(job_type)
    try:
        if handler is None:
            raise RuntimeError(f"no handler registered for job_type {job_type!r}")
        await handler(payload)
    except Exception as exc:  # noqa: BLE001 — record and let backoff/retry decide
        logger.exception("Job %s (%s) failed", job_id, job_type)
        async with AsyncSessionLocal() as session:
            await mark_failed(session, job_id, str(exc))
            await session.commit()
        return True

    async with AsyncSessionLocal() as session:
        await mark_done(session, job_id)
        await session.commit()
    logger.info("Job %s (%s) done", job_id, job_type)
    return True


async def run_forever() -> None:
    worker_id = f"worker-{os.getpid()}"
    logger.info("Worker %s started; polling every %.1fs", worker_id, POLL_INTERVAL_SECONDS)
    while True:
        did_work = await process_one(worker_id)
        if not did_work:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
