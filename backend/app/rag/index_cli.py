"""CLI: index a local directory into the RAG memory (for verification/dev).

Usage (from backend/, venv active, OPENAI_API_KEY set):
    python -m app.rag.index_cli ../backend/app --name local/sample
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from app.db.session import AsyncSessionLocal
from app.rag.indexer import get_or_create_repository, index_directory


async def _run(path: str, project_id: int, name: str) -> None:
    async with AsyncSessionLocal() as session:
        repo = await get_or_create_repository(session, project_id, name)
        stats = await index_directory(session, repo, Path(path))
        await session.commit()
    print(
        f"Indexed {stats.files} files, {stats.chunks} chunks, "
        f"{stats.tokens} tokens, ${stats.cost_usd:.4f}"
    )


def main() -> None:
    logging.basicConfig(level="INFO")
    parser = argparse.ArgumentParser(description="Index a directory into RAG memory")
    parser.add_argument("path", help="directory to index")
    parser.add_argument("--project-id", type=int, default=999999)
    parser.add_argument("--name", default="local/sample")
    args = parser.parse_args()
    asyncio.run(_run(args.path, args.project_id, args.name))


if __name__ == "__main__":
    main()
