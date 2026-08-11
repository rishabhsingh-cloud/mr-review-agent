"""Incremental re-index driven by a GitLab push.

Fetches only the changed files' new content via the GitLab API and refreshes
their chunks; deletes chunks for removed files. Cheap — a merge touches a
handful of files, not the whole repo.

Guard: only repos that have ALREADY been fully indexed are updated incrementally.
Pushing a couple of files into a never-indexed repo would leave a misleading
partial index, so those are skipped (run index_gitlab_cli for a first full index).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CodeChunk, Repository
from app.gitlab.client import get_project
from app.rag.indexer import (
    LANG_BY_EXT,
    MAX_FILE_BYTES,
    IndexStats,
    get_or_create_repository,
    index_files,
)

logger = logging.getLogger(__name__)


def _fetch_file(project, path: str, ref: str) -> str | None:
    try:
        pf = project.files.get(file_path=path, ref=ref)
        return pf.decode().decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 — deleted/binary/inaccessible files just skip
        return None


async def _is_indexed(session: AsyncSession, repo: Repository) -> bool:
    if repo.last_indexed_sha:
        return True
    existing = await session.scalar(
        select(func.count()).select_from(CodeChunk).where(CodeChunk.repository_id == repo.id)
    )
    return bool(existing)


async def reindex_from_push(
    session: AsyncSession,
    project_id: int,
    changed: list[str],
    removed: list[str],
    after_sha: str,
) -> IndexStats | None:
    """Refresh chunks for changed files + drop removed files. Returns None if
    the repo isn't indexed yet (skipped)."""
    project = await asyncio.to_thread(get_project, project_id)
    path_ns = getattr(project, "path_with_namespace", str(project_id))
    repo = await get_or_create_repository(session, project_id, path_ns)

    if not await _is_indexed(session, repo):
        logger.info(
            "Skipping incremental reindex for %s — not indexed yet (run index_gitlab_cli)",
            path_ns,
        )
        return None

    files: dict[str, str] = {}
    for path in changed:
        if Path(path).suffix.lower() not in LANG_BY_EXT:
            continue
        content = await asyncio.to_thread(_fetch_file, project, path, after_sha)
        if content is None or len(content.encode("utf-8", "ignore")) > MAX_FILE_BYTES:
            continue
        files[path] = content

    stats = await index_files(
        session, repo, files, commit_sha=after_sha, replace=True, removed=removed
    )
    logger.info(
        "Reindexed %s @ %s: %d files updated, %d removed, %d chunks, $%.4f",
        path_ns, after_sha[:8], stats.files, stats.removed, stats.chunks, stats.cost_usd,
    )
    return stats
