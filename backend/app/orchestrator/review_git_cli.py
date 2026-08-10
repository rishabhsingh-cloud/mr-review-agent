"""Review a REAL git diff from any local repo — no GitLab needed.

Pulls a diff via `git`, converts it to the MR change-set shape, runs the full
review, and prints findings + the gate decision. Nothing is posted or persisted
(dry run). Optionally index the repo first (`--index`) for RAG grounding.

Usage (from backend/, venv active, OPENAI_API_KEY set, db up):
    # review uncommitted working changes vs HEAD
    python -m app.orchestrator.review_git_cli /path/to/repo

    # review a commit/branch range
    python -m app.orchestrator.review_git_cli /path/to/repo --base main --head my-branch

    # ground the review on the repo's code first (costs embeddings)
    python -m app.orchestrator.review_git_cli /path/to/repo --base HEAD~3 --index
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import subprocess
from pathlib import Path

from app.db.session import AsyncSessionLocal
from app.llm.factory import get_provider
from app.orchestrator.render import build_summary, format_comment, to_inline_comments
from app.orchestrator.service import review_changes
from app.rag.indexer import get_or_create_repository, index_directory

_EVAL_PROJECT_ID = 777777


def _git(repo: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _rev(repo: str, ref: str) -> str:
    try:
        return _git(repo, "rev-parse", ref).strip()
    except Exception:
        return ref


def parse_git_diff(text: str) -> list[dict]:
    """Split raw `git diff` output into per-file change dicts."""
    sections: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            if cur is not None:
                sections.append(cur)
            cur = {"header": line, "body": []}
        elif cur is not None:
            cur["body"].append(line)
    if cur is not None:
        sections.append(cur)

    changes: list[dict] = []
    for s in sections:
        old_path = new_path = None
        new_file = deleted = renamed = False
        diff_lines: list[str] = []
        in_hunk = False
        for line in s["body"]:
            if line.startswith("new file mode"):
                new_file = True
            elif line.startswith("deleted file mode"):
                deleted = True
            elif line.startswith("rename from "):
                renamed = True
                old_path = line[len("rename from "):].strip()
            elif line.startswith("rename to "):
                new_path = line[len("rename to "):].strip()
            elif line.startswith("--- "):
                p = line[4:].strip()
                old_path = None if p == "/dev/null" else p[2:] if p[:2] in ("a/", "b/") else p
            elif line.startswith("+++ "):
                p = line[4:].strip()
                new_path = None if p == "/dev/null" else p[2:] if p[:2] in ("a/", "b/") else p
            if line.startswith("@@"):
                in_hunk = True
            if in_hunk:
                diff_lines.append(line)

        if old_path is None or new_path is None:
            m = re.match(r"diff --git a/(.*) b/(.*)", s["header"])
            if m:
                old_path = old_path or m.group(1)
                new_path = new_path or m.group(2)

        changes.append({
            "old_path": old_path or new_path or "unknown",
            "new_path": new_path or old_path or "unknown",
            "new_file": new_file,
            "deleted_file": deleted,
            "renamed_file": renamed,
            "diff": "\n".join(diff_lines),
        })
    return changes


async def _run(repo: str, base: str | None, head: str | None, scope: str, do_index: bool) -> None:
    if base and head:
        diff_text = _git(repo, "diff", "--no-color", f"{base}..{head}")
        base_sha, head_sha = _rev(repo, base), _rev(repo, head)
    elif base:
        diff_text = _git(repo, "diff", "--no-color", base)
        base_sha, head_sha = _rev(repo, base), "WORKING"
    else:
        diff_text = _git(repo, "diff", "--no-color", "HEAD")
        base_sha, head_sha = _rev(repo, "HEAD"), "WORKING"

    file_changes = parse_git_diff(diff_text)
    if not file_changes:
        print("No changes found for that range. Try --base HEAD~1 or make some edits.")
        return

    changes = {
        "diff_refs": {"base_sha": base_sha, "start_sha": base_sha, "head_sha": head_sha},
        "changes": file_changes,
    }

    provider = get_provider()
    async with AsyncSessionLocal() as session:
        repo_row = await get_or_create_repository(
            session, _EVAL_PROJECT_ID, f"local/{Path(repo).name}"
        )
        if do_index:
            print("Indexing repo for grounding (this makes embedding calls)...")
            stats = await index_directory(session, repo_row, Path(repo))
            await session.commit()
            print(f"  indexed {stats.files} files, {stats.chunks} chunks, ${stats.cost_usd:.4f}")

        state, _refs, _fd = await review_changes(session, provider, repo_row, changes, scope)
        await session.rollback()

    aggregated = state["aggregated"]
    decision = state["decision"]
    print(f"\nReviewed {len(file_changes)} changed file(s).")
    print("=" * 70)
    print(build_summary(decision, aggregated, scope))
    print("=" * 70)

    inline = {(c.file_path, c.new_line) for c in to_inline_comments(aggregated)}
    for agg in aggregated:
        f = agg.finding
        loc = f"{f.file_path}:{f.line_start}" if f.file_path else "(no line)"
        pin = "📍 inline" if (f.file_path, f.line_start) in inline else "↪ summary-only"
        print(f"\n[{loc}] {pin}")
        print(format_comment(agg))


def main() -> None:
    logging.basicConfig(level="WARNING")
    parser = argparse.ArgumentParser(description="Review a local git diff")
    parser.add_argument("repo", help="path to a local git repository")
    parser.add_argument("--base", default=None, help="base ref (default: HEAD)")
    parser.add_argument("--head", default=None, help="head ref (default: working tree)")
    parser.add_argument("--scope", default="all")
    parser.add_argument("--index", action="store_true", help="index the repo for RAG grounding first")
    args = parser.parse_args()
    asyncio.run(_run(args.repo, args.base, args.head, args.scope, args.index))


if __name__ == "__main__":
    main()
