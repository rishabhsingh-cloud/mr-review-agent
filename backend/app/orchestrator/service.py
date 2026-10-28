"""Review orchestration service.

`review_changes` runs the graph over an MR change set (no GitLab side effects) —
used by both the live worker and the dry-run CLI. `run_live_review` adds the
GitLab fetch, publishing, and persistence around it.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.aggregator import AUTO_POST
from app.agents.base import AgentResult
from app.gitlab.client import get_merge_request, get_mr_changes, get_project
from app.gitlab.diff import DiffRefs, FileDiff, parse_mr_changes
from app.llm.factory import get_provider
from app.llm.provider import LLMProvider
from app.orchestrator.context import build_file_units, make_retriever
from app.orchestrator.graph import run_review
from app.orchestrator.persistence import create_review, finalize_review
from app.orchestrator.render import build_summary, to_inline_comments
from app.rag.indexer import get_or_create_repository
from app.db.models import Repository
from app.config import get_settings
from app.telemetry.cost import BudgetExceeded, ensure_budget
from app.telemetry.events import (
    new_trace_id,
    record_agent_calls,
    record_review_event,
)

logger = logging.getLogger(__name__)


def _total_cost(agent_results: list[AgentResult]) -> float:
    return sum(r.response.cost_usd for r in agent_results if r.response is not None)


async def _record_and_finalize(session, review, trace_id, state) -> float:
    """Record telemetry for the run and finalize the review record."""
    agent_results = state.get("agent_results", [])
    aggregated = state["aggregated"]
    decision = state["decision"]
    cost = _total_cost(agent_results)
    await record_agent_calls(session, review.id, trace_id, agent_results)
    await record_review_event(session, review.id, trace_id, decision, cost)
    await finalize_review(session, review, decision, aggregated, cost_usd=cost)
    return cost


async def review_changes(
    session: AsyncSession,
    provider: LLMProvider,
    repo: Repository,
    changes: dict,
    scope: str,
) -> tuple[dict, DiffRefs, list[FileDiff]]:
    """Run the review graph over a change set (per-file). Returns (state, refs, file_diffs)."""
    refs, file_diffs = parse_mr_changes(changes)
    files = build_file_units(changes, get_settings().review_max_file_diff_chars)
    retriever = make_retriever(session, repo.id)
    state = await run_review(provider, retriever, files=files, scope=scope)
    return state, refs, file_diffs


async def run_live_review(
    session: AsyncSession,
    *,
    project_id: int,
    mr_iid: int,
    scope: str,
    triggered_by: str | None,
) -> None:
    """End-to-end live review: fetch → graph → publish (if auto) → persist."""
    provider = get_provider()
    settings = get_settings()
    trace_id = new_trace_id()

    # GitLab calls are synchronous (python-gitlab) — run them off the event loop.
    project = await asyncio.to_thread(get_project, project_id)
    mr = await asyncio.to_thread(get_merge_request, project_id, mr_iid)
    changes = await asyncio.to_thread(get_mr_changes, mr)

    repo = await get_or_create_repository(
        session, project_id, getattr(project, "path_with_namespace", str(project_id))
    )
    head_sha = (changes.get("diff_refs") or {}).get("head_sha", "")

    review = await create_review(
        session,
        repository_id=repo.id,
        project_id=project_id,
        mr_iid=mr_iid,
        head_sha=head_sha,
        scope=scope,
        triggered_by=triggered_by,
        trace_id=trace_id,
    )

    # Budget guard — block the review before spending if today's cap is hit.
    try:
        await ensure_budget(session, settings.daily_budget_usd)
    except BudgetExceeded as exc:
        review.status = "blocked"
        review.summary = str(exc)
        review.completed_at = review.started_at
        logger.warning("Review of MR !%s blocked: %s", mr_iid, exc)
        return

    state, refs, file_diffs = await review_changes(session, provider, repo, changes, scope)
    aggregated = state["aggregated"]
    decision = state["decision"]

    if decision.action == AUTO_POST and aggregated:
        from app.gitlab.publish import GitLabPublisher

        publisher = GitLabPublisher(mr, refs, file_diffs, post_as=triggered_by)
        comments = to_inline_comments(aggregated)
        summary = build_summary(decision, aggregated, scope)
        result = await asyncio.to_thread(publisher.publish, comments, summary)
        logger.info(
            "Posted %d inline (%d fell back) for MR !%s",
            result.inline_posted, result.fell_back, mr_iid,
        )

    cost = await _record_and_finalize(session, review, trace_id, state)
    logger.info(
        "Review of MR !%s complete: %d finding(s), gate=%s, $%.4f",
        mr_iid, decision.total, decision.action, cost,
    )


async def review_and_record(
    session: AsyncSession,
    repo: Repository,
    changes: dict,
    scope: str,
    *,
    project_id: int,
    mr_iid: int,
    triggered_by: str | None = None,
) -> tuple[dict, int]:
    """Run a review over a change set and PERSIST it + telemetry (no GitLab).

    Used by the dry-run CLI with --record to exercise the Time/Truth lanes
    offline. Returns (graph_state, review_id).
    """
    provider = get_provider()
    trace_id = new_trace_id()
    head_sha = (changes.get("diff_refs") or {}).get("head_sha", "")
    review = await create_review(
        session,
        repository_id=repo.id,
        project_id=project_id,
        mr_iid=mr_iid,
        head_sha=head_sha,
        scope=scope,
        triggered_by=triggered_by,
        trace_id=trace_id,
    )
    state, _refs, _file_diffs = await review_changes(session, provider, repo, changes, scope)
    await _record_and_finalize(session, review, trace_id, state)
    return state, review.id
