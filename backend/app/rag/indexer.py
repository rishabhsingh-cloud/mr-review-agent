"""Index repository code into the `code_chunks` memory lane.

Supports a full index of a directory and incremental re-indexing of a set of
files (delete-then-insert per path). Embeddings come from the OpenAI Embedder;
chunks are stored with their 256-dim vector, and the generated tsvector column
handles full-text automatically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import CodeChunk, Repository
from app.llm.embeddings import Embedder
from app.rag.chunker import chunk_code

logger = logging.getLogger(__name__)

# Extension -> language label. Files with other extensions are skipped.
LANG_BY_EXT: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go",
    ".java": "java", ".rb": "ruby", ".php": "php", ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".kt": "kotlin", ".swift": "swift", ".scala": "scala", ".sql": "sql",
    ".sh": "shell", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown",
}
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
    "build", ".next", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}
MAX_FILE_BYTES = 500_000


@dataclass
class IndexStats:
    files: int = 0
    chunks: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    removed: int = 0


async def get_or_create_repository(
    session: AsyncSession, gitlab_project_id: int, path_with_namespace: str
) -> Repository:
    repo = (
        await session.execute(
            select(Repository).where(Repository.gitlab_project_id == gitlab_project_id)
        )
    ).scalar_one_or_none()
    if repo is None:
        repo = Repository(
            gitlab_project_id=gitlab_project_id,
            path_with_namespace=path_with_namespace,
        )
        session.add(repo)
        await session.flush()
    return repo


def _iter_source_files(root: Path):
    for path in root.rglob("*"):
        if path.is_dir():
            if path.name in SKIP_DIRS:
                # Prevent descending further by skipping — rglob can't prune, so
                # we filter on the path parts below instead.
                continue
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in LANG_BY_EXT:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path


async def index_files(
    session: AsyncSession,
    repo: Repository,
    files: dict[str, str],
    *,
    commit_sha: str | None = None,
    replace: bool = True,
    removed: list[str] | None = None,
) -> IndexStats:
    """Index a mapping of {relative_path: content}. If replace, existing chunks
    for those paths are deleted first (incremental re-index). Any paths in
    ``removed`` have their chunks deleted too (files deleted from the repo)."""
    settings = get_settings()
    embedder = Embedder()
    stats = IndexStats()

    to_delete: set[str] = set(files.keys()) if replace else set()
    if removed:
        to_delete |= set(removed)
    if to_delete:
        await session.execute(
            delete(CodeChunk).where(
                CodeChunk.repository_id == repo.id,
                CodeChunk.file_path.in_(list(to_delete)),
            )
        )
    stats.removed = len(removed or [])

    # Build all chunks first, then embed in batches to amortize API calls.
    pending: list[tuple[str, str, object]] = []  # (file_path, language, Chunk)
    for file_path, content in files.items():
        language = LANG_BY_EXT.get(Path(file_path).suffix.lower())
        for chunk in chunk_code(
            content,
            max_tokens=settings.rag_chunk_max_tokens,
            overlap_lines=settings.rag_overlap_lines,
        ):
            pending.append((file_path, language, chunk))
        stats.files += 1

    batch = settings.rag_embed_batch
    for start in range(0, len(pending), batch):
        group = pending[start : start + batch]
        result = await embedder.embed([c.content for (_, _, c) in group])
        stats.tokens += result.tokens
        stats.cost_usd += result.cost_usd
        for (file_path, language, chunk), vector in zip(group, result.vectors):
            session.add(
                CodeChunk(
                    repository_id=repo.id,
                    file_path=file_path,
                    language=language,
                    symbols=chunk.symbols,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                    embedding=vector,
                    token_count=chunk.token_count,
                    commit_sha=commit_sha,
                )
            )
            stats.chunks += 1

    if commit_sha:
        repo.last_indexed_sha = commit_sha
    await session.flush()
    return stats


async def index_directory(
    session: AsyncSession,
    repo: Repository,
    root: Path,
    *,
    commit_sha: str | None = None,
) -> IndexStats:
    """Full index of a local directory tree. Wipes the repo's existing chunks."""
    await session.execute(delete(CodeChunk).where(CodeChunk.repository_id == repo.id))

    files: dict[str, str] = {}
    for path in _iter_source_files(root):
        try:
            files[str(path.relative_to(root))] = path.read_text(
                encoding="utf-8", errors="ignore"
            )
        except OSError:
            continue

    stats = await index_files(session, repo, files, commit_sha=commit_sha, replace=False)
    logger.info(
        "Indexed %s: %d files, %d chunks, %d tokens, $%.4f",
        repo.path_with_namespace, stats.files, stats.chunks, stats.tokens, stats.cost_usd,
    )
    return stats
