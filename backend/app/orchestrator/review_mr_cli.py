"""CLI: review a REAL GitLab MR manually (before the webhook is set up).

Default is a dry run (fetch + review + print, nothing posted/saved). Pass --post
to run the full live path (posts inline comments if the gate is auto_post, and
persists the review + telemetry) — the same code the webhook triggers.

Usage (from backend/, venv active, GITLAB_URL/GITLAB_TOKEN set, db up):
    python -m app.orchestrator.review_mr_cli --project 123 --mr 7
    python -m app.orchestrator.review_mr_cli --project 123 --mr 7 --scope security
    python -m app.orchestrator.review_mr_cli --project 123 --mr 7 --post
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.db.session import AsyncSessionLocal
from app.gitlab.client import get_merge_request, get_mr_changes, get_project
from app.llm.factory import get_provider
from app.orchestrator.render import build_summary, format_comment, to_inline_comments
from app.orchestrator.service import review_changes, run_live_review
from app.rag.indexer import get_or_create_repository


async def _dry(project_id: int, mr_iid: int, scope: str) -> None:
    provider = get_provider()
    project = await asyncio.to_thread(get_project, project_id)
    mr = await asyncio.to_thread(get_merge_request, project_id, mr_iid)
    changes = await asyncio.to_thread(get_mr_changes, mr)

    async with AsyncSessionLocal() as session:
        repo = await get_or_create_repository(
            session, project_id, getattr(project, "path_with_namespace", str(project_id))
        )
        state, _refs, _fd = await review_changes(session, provider, repo, changes, scope)
        await session.rollback()

    aggregated = state["aggregated"]
    decision = state["decision"]
    print("\n" + "=" * 70)
    print(build_summary(decision, aggregated, scope))
    print("=" * 70)
    inline = {(c.file_path, c.new_line) for c in to_inline_comments(aggregated)}
    for agg in aggregated:
        f = agg.finding
        loc = f"{f.file_path}:{f.line_start}" if f.file_path else "(no line)"
        pin = "📍 inline" if (f.file_path, f.line_start) in inline else "↪ summary-only"
        print(f"\n[{loc}] {pin}")
        print(format_comment(agg))
    print("\n(dry run — nothing posted. Re-run with --post to publish.)")


async def _post(project_id: int, mr_iid: int, scope: str) -> None:
    async with AsyncSessionLocal() as session:
        await run_live_review(
            session, project_id=project_id, mr_iid=mr_iid, scope=scope, triggered_by="cli"
        )
        await session.commit()
    print("Done — review persisted; inline comments posted if the gate was auto_post.")


def main() -> None:
    logging.basicConfig(level="INFO")
    parser = argparse.ArgumentParser(description="Review a real GitLab MR")
    parser.add_argument("--project", type=int, required=True, help="GitLab project id")
    parser.add_argument("--mr", type=int, required=True, help="merge request iid")
    parser.add_argument("--scope", default="all")
    parser.add_argument("--post", action="store_true", help="actually post + persist")
    args = parser.parse_args()

    if args.post:
        asyncio.run(_post(args.project, args.mr, args.scope))
    else:
        asyncio.run(_dry(args.project, args.mr, args.scope))


if __name__ == "__main__":
    main()
