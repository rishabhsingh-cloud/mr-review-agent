"""Build reviewer inputs from an MR change set: the diff text, a retrieval
query, the per-MR RAG retriever, and formatted context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CodeChunk
from app.rag.retriever import hybrid_search

_MAX_QUERY_CHARS = 1500

# Files not worth reviewing (generated, lock, binary/asset).
_SKIP_SUFFIXES = (
    ".lock", ".min.js", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".ttf", ".snap",
)
_SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "go.sum", "composer.lock", "Cargo.lock",
}


def _is_skippable(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in _SKIP_NAMES or path.lower().endswith(_SKIP_SUFFIXES)


def build_file_units(changes: dict, max_chars: int = 40000) -> list[dict]:
    """Split a change set into per-file review units {path, diff_text}.

    Deleted, generated/lock, and empty-diff files are skipped; oversized diffs
    are truncated to keep a single file's prompt bounded.
    """
    units: list[dict] = []
    for ch in changes.get("changes", []):
        if ch.get("deleted_file"):
            continue
        path = ch.get("new_path") or ch.get("old_path") or ""
        if not path or _is_skippable(path):
            continue
        diff = ch.get("diff", "")
        if not diff.strip():
            continue
        text = f"--- a/{ch.get('old_path')}\n+++ b/{ch.get('new_path')}\n{diff}"
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [diff truncated for length]"
        units.append({"path": path, "diff_text": text})
    return units


def build_diff_text(changes: dict) -> str:
    """Concatenate per-file unified diffs with headers for the agents."""
    parts: list[str] = []
    for ch in changes.get("changes", []):
        parts.append(
            f"--- a/{ch.get('old_path')}\n+++ b/{ch.get('new_path')}\n{ch.get('diff', '')}"
        )
    return "\n".join(parts)


def diff_query(diff_text: str) -> str:
    """Derive a retrieval query from the added lines of a diff."""
    added: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:].strip())
        elif line.startswith("+++"):
            added.append(line[4:].strip())  # keep the path as a keyword
    return "\n".join(added)[:_MAX_QUERY_CHARS]


def format_chunks(chunks: list[CodeChunk]) -> str:
    if not chunks:
        return ""
    blocks = [
        f"// {c.file_path}:{c.start_line}-{c.end_line}\n{c.content}" for c in chunks
    ]
    return "\n\n".join(blocks)


def make_retriever(
    session: AsyncSession, repo_id: int
) -> Callable[[str], Awaitable[str]]:
    """Return an async retriever(diff_text) -> formatted context string.

    If the repo has no indexed chunks, returns "" without any embedding call
    (avoids paying for query embeddings when there's nothing to search).
    """
    cache: dict[str, bool] = {}

    async def retrieve(diff_text: str) -> str:
        if "has_chunks" not in cache:
            exists = await session.scalar(
                select(CodeChunk.id).where(CodeChunk.repository_id == repo_id).limit(1)
            )
            cache["has_chunks"] = exists is not None
        if not cache["has_chunks"]:
            return ""
        query = diff_query(diff_text) or diff_text[:_MAX_QUERY_CHARS]
        if not query.strip():
            return ""
        chunks = await hybrid_search(session, repo_id, query)
        return format_chunks(chunks)

    return retrieve
