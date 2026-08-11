"""Index a GitLab repo into RAG memory by pulling its archive via the API.

No local checkout or `git` binary needed — downloads the repository archive at a
ref (default branch by default), extracts it, and indexes it. Repeatable; this
is also the basis for re-indexing on merge later.

Usage (inside the worker container, GITLAB_URL/GITLAB_TOKEN set):
    docker compose exec worker python -m app.rag.index_gitlab_cli --project 123
    docker compose exec worker python -m app.rag.index_gitlab_cli --project 123 --ref main
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import tarfile
import tempfile
from pathlib import Path

from app.db.session import AsyncSessionLocal
from app.gitlab.client import get_project
from app.rag.indexer import get_or_create_repository, index_directory

logger = logging.getLogger(__name__)


def _head_sha(project, ref: str) -> str | None:
    try:
        commits = project.commits.list(ref_name=ref, per_page=1, iterator=False)
        return commits[0].id if commits else None
    except Exception:  # noqa: BLE001
        return None


async def _run(project_id: int, ref: str | None, name: str | None) -> None:
    project = get_project(project_id)
    default_branch = getattr(project, "default_branch", "main") or "main"
    ref = ref or default_branch
    path_ns = name or getattr(project, "path_with_namespace", str(project_id))
    head_sha = _head_sha(project, ref)

    print(f"Downloading archive of {path_ns}@{ref} ...")
    data = project.repository_archive(format="tar.gz", sha=ref)

    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            tf.extractall(tmp, filter="data")  # filter guards against path traversal
        subdirs = [p for p in Path(tmp).iterdir() if p.is_dir()]
        root = subdirs[0] if subdirs else Path(tmp)

        async with AsyncSessionLocal() as session:
            repo = await get_or_create_repository(session, project_id, path_ns)
            stats = await index_directory(session, repo, root, commit_sha=head_sha)
            await session.commit()

    sha_disp = (head_sha or ref)[:8]
    print(
        f"Indexed {path_ns}@{sha_disp}: {stats.files} files, {stats.chunks} chunks, "
        f"{stats.tokens} tokens, ${stats.cost_usd:.4f}"
    )


def main() -> None:
    logging.basicConfig(level="INFO")
    parser = argparse.ArgumentParser(description="Index a GitLab repo via its API")
    parser.add_argument("--project", type=int, required=True, help="GitLab project id")
    parser.add_argument("--ref", default=None, help="branch/tag/sha (default: repo default branch)")
    parser.add_argument("--name", default=None, help="override stored path (default: path_with_namespace)")
    args = parser.parse_args()
    asyncio.run(_run(args.project, args.ref, args.name))


if __name__ == "__main__":
    main()
