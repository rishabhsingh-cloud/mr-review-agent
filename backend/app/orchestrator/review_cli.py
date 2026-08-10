"""Dry-run a real review over a saved MR change set — no GitLab, no publishing.

Uses the configured provider (your OpenAI key) and, if present, the indexed
`local/sample` repo for grounding context. Prints findings, the gate decision,
and the comments/summary that WOULD be posted. Nothing is written to the MR;
the DB transaction is rolled back.

Usage (from backend/, venv active, OPENAI_API_KEY set):
    python -m app.orchestrator.review_cli ../samples/mr_changes.json --scope all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from app.db.session import AsyncSessionLocal
from app.llm.factory import get_provider
from app.orchestrator.render import build_summary, format_comment, to_inline_comments
from app.orchestrator.service import review_and_record, review_changes
from app.rag.indexer import get_or_create_repository


async def _run(changes_path: str, scope: str, project_id: int, name: str, record: bool) -> None:
    changes = json.loads(Path(changes_path).read_text())

    async with AsyncSessionLocal() as session:
        repo = await get_or_create_repository(session, project_id, name)
        if record:
            state, review_id = await review_and_record(
                session, repo, changes, scope, project_id=project_id, mr_iid=0
            )
            await session.commit()
            print(f"(recorded review id={review_id} + telemetry to the database)")
        else:
            provider = get_provider()
            state, _refs, _file_diffs = await review_changes(
                session, provider, repo, changes, scope
            )
            await session.rollback()  # dry run — persist nothing

    aggregated = state["aggregated"]
    decision = state["decision"]

    print("\n" + "=" * 70)
    print(build_summary(decision, aggregated, scope))
    print("=" * 70)
    if not aggregated:
        print("\n(no findings)")
        return

    inline = {(c.file_path, c.new_line) for c in to_inline_comments(aggregated)}
    for agg in aggregated:
        f = agg.finding
        loc = f"{f.file_path}:{f.line_start}" if f.file_path else "(no line)"
        pin = "📍 inline" if (f.file_path, f.line_start) in inline else "↪ summary-only"
        print(f"\n[{loc}] {pin}")
        print(format_comment(agg))


def main() -> None:
    logging.basicConfig(level="INFO")
    parser = argparse.ArgumentParser(description="Dry-run a review over a change set")
    parser.add_argument("changes", help="path to an MR changes JSON file")
    parser.add_argument("--scope", default="all")
    parser.add_argument("--project-id", type=int, default=999999)
    parser.add_argument("--name", default="local/sample")
    parser.add_argument(
        "--record", action="store_true",
        help="persist the review + telemetry to the DB (default: dry run, rolled back)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.changes, args.scope, args.project_id, args.name, args.record))


if __name__ == "__main__":
    main()
