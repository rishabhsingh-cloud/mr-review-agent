"""ORM models for the three data lanes (memory / truth / time) and the job queue.

The initial schema is created by the hand-written Alembic migration
(0001_initial_schema) because several objects — the pgvector column, the
pgvectorscale DiskANN index, the generated tsvector column, and the TimescaleDB
hypertable — are not expressible via plain autogenerate. These models mirror that
schema for use by the application at runtime.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db.base import Base

EMBEDDING_DIM = get_settings().embedding_dim


def _now_col() -> Mapped[datetime]:
    return mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


# ─────────────────────────── Lane 2 — Truth ───────────────────────────


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    gitlab_project_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    path_with_namespace: Mapped[str] = mapped_column(Text, nullable=False)
    default_branch: Mapped[str] = mapped_column(Text, nullable=False, server_default="main")
    last_indexed_sha: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()

    chunks: Mapped[list[CodeChunk]] = relationship(back_populates="repository")


class WebhookDelivery(Base):
    """Idempotency ledger — one row per processed webhook delivery."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    delivery_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    event_type: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = _now_col()


class MrReviewRecord(Base):
    __tablename__ = "mr_review_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repository_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("repositories.id", ondelete="SET NULL")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mr_iid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    head_sha: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(Text)
    command_scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="all")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    summary: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(Text)
    total_cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = _now_col()
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    findings: Mapped[list[FindingRecord]] = relationship(back_populates="review")


class FindingRecord(Base):
    __tablename__ = "finding_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mr_review_records.id", ondelete="CASCADE"), nullable=False
    )
    agent_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    rationale: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    created_at: Mapped[datetime] = _now_col()

    review: Mapped[MrReviewRecord] = relationship(back_populates="findings")


class HitlReview(Base):
    __tablename__ = "hitl_reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mr_review_records.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = _now_col()


class HitlFeedback(Base):
    __tablename__ = "hitl_feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    finding_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("finding_records.id", ondelete="CASCADE")
    )
    review_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("mr_review_records.id", ondelete="CASCADE")
    )
    feedback_type: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()


# ─────────────────────────── Lane 1 — Memory ───────────────────────────


class CodeChunk(Base):
    __tablename__ = "code_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(Text)
    symbols: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Generated column — read-only from the ORM's perspective.
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    commit_sha: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()

    repository: Mapped[Repository] = relationship(back_populates="chunks")


# ─────────────────────────── Job queue ───────────────────────────


class Job(Base):
    """Postgres-backed background job (replaces Redis/ARQ)."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    run_after: Mapped[datetime] = _now_col()
    claimed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col()


# ─────────────────────────── Lane 3 — Time ───────────────────────────


class AgentEvent(Base):
    """Immutable, time-ordered event — converted to a TimescaleDB hypertable.

    The primary key includes the partitioning column ``ts`` as TimescaleDB
    requires for any unique constraint on a hypertable.
    """

    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True, nullable=False, server_default=text("now()")
    )
    trace_id: Mapped[str | None] = mapped_column(Text)
    span_id: Mapped[str | None] = mapped_column(Text)
    parent_span_id: Mapped[str | None] = mapped_column(Text)
    review_id: Mapped[int | None] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    agent_type: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    outcome: Mapped[str | None] = mapped_column(Text)
    event_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
