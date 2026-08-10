"""Enqueue and claim background jobs using a Postgres table.

Claiming uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so multiple workers never
grab the same job. Failed jobs are retried with a simple linear backoff until
``max_attempts`` is reached.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job

RETRY_BACKOFF_SECONDS = 30


async def enqueue(session: AsyncSession, job_type: str, payload: dict) -> int:
    """Insert a queued job and return its id. Caller commits."""
    job = Job(job_type=job_type, payload=payload)
    session.add(job)
    await session.flush()
    return job.id


async def claim_next(session: AsyncSession, worker_id: str) -> Job | None:
    """Atomically claim the oldest runnable job, marking it 'running'.

    Must be called inside a transaction the caller then commits to release the
    row lock while leaving status='running'.
    """
    stmt = (
        select(Job)
        .where(Job.status == "queued", Job.run_after <= func.now())
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.claimed_at = datetime.now(timezone.utc)
    job.claimed_by = worker_id
    job.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return job


async def mark_done(session: AsyncSession, job_id: int) -> None:
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.status = "done"
    job.updated_at = datetime.now(timezone.utc)
    await session.flush()


async def mark_failed(session: AsyncSession, job_id: int, error: str) -> None:
    """Requeue with backoff if attempts remain, else mark failed permanently."""
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.last_error = error[:4000]
    job.updated_at = datetime.now(timezone.utc)
    if job.attempts >= job.max_attempts:
        job.status = "failed"
    else:
        job.status = "queued"
        job.run_after = datetime.now(timezone.utc) + timedelta(
            seconds=RETRY_BACKOFF_SECONDS * job.attempts
        )
    await session.flush()
