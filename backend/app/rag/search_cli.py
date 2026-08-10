"""CLI: hybrid-search the RAG memory (for verification/dev).

Usage (from backend/, venv active):
    python -m app.rag.search_cli "how are webhook tokens verified"
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db.models import Repository
from app.db.session import AsyncSessionLocal
from app.rag.retriever import hybrid_search


async def _run(query: str, project_id: int, k: int) -> None:
    async with AsyncSessionLocal() as session:
        repo = (
            await session.execute(
                select(Repository).where(Repository.gitlab_project_id == project_id)
            )
        ).scalar_one_or_none()
        if repo is None:
            print(f"No indexed repo for project id {project_id}. Run index_cli first.")
            return
        results = await hybrid_search(session, repo.id, query, top_k=k)

    if not results:
        print("No results.")
        return
    for r in results:
        print(f"\n=== {r.file_path}:{r.start_line}-{r.end_line} "
              f"(lang={r.language}, symbols={r.symbols}) ===")
        snippet = "\n".join(r.content.splitlines()[:8])
        print(snippet)


def main() -> None:
    parser = argparse.ArgumentParser(description="Hybrid-search RAG memory")
    parser.add_argument("query", help="natural-language or keyword query")
    parser.add_argument("--project-id", type=int, default=999999)
    parser.add_argument("--k", type=int, default=8)
    args = parser.parse_args()
    asyncio.run(_run(args.query, args.project_id, args.k))


if __name__ == "__main__":
    main()
