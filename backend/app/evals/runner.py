"""Run the reviewer over labeled eval cases and report recall + false positives.

Each case JSON has a change set, a scope, and `expected` findings (file + a
keyword expected in the finding). Recall = expected findings that were matched;
'extra' findings are ones not tied to any expected label — on a `clean` case
(expected == []) every extra is a false positive.

Matching is approximate (keyword-in-text), so treat this as a tuning aid, not a
strict gate. Uses the configured provider (real LLM calls).

Usage (from backend/, venv active, OPENAI_API_KEY set, db up):
    python -m app.evals.runner
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from app.db.session import AsyncSessionLocal
from app.llm.factory import get_provider
from app.orchestrator.service import review_changes
from app.rag.indexer import get_or_create_repository

_CASES_DIR = Path(__file__).parent / "cases"
_EVAL_PROJECT_ID = 888888


def _matches(finding, expected: dict) -> bool:
    if expected.get("file_path") and finding.file_path != expected["file_path"]:
        return False
    keyword = (expected.get("keyword") or "").lower()
    if keyword:
        haystack = " ".join(
            filter(None, [finding.title, finding.category, finding.rationale])
        ).lower()
        if keyword not in haystack:
            return False
    return True


async def _run() -> None:
    provider = get_provider()
    cases = sorted(_CASES_DIR.glob("*.json"))
    total_expected = total_matched = total_extra = 0

    async with AsyncSessionLocal() as session:
        repo = await get_or_create_repository(session, _EVAL_PROJECT_ID, "eval/harness")

        for path in cases:
            case = json.loads(path.read_text())
            state, _refs, _fd = await review_changes(
                session, provider, repo, case["changes"], case.get("scope", "all")
            )
            await session.rollback()

            findings = [a.finding for a in state["aggregated"]]
            expected = case.get("expected", [])

            matched = sum(
                1 for e in expected if any(_matches(f, e) for f in findings)
            )
            matched_findings = {
                id(f) for e in expected for f in findings if _matches(f, e)
            }
            extra = [f for f in findings if id(f) not in matched_findings]

            total_expected += len(expected)
            total_matched += matched
            total_extra += len(extra)

            recall = f"{matched}/{len(expected)}" if expected else "n/a"
            print(f"\n### {case['name']}  (scope={case.get('scope', 'all')})")
            print(f"    recall: {recall}   extra findings: {len(extra)}")
            for f in extra:
                loc = f.file_path or "-"
                print(f"      + [{f.severity.value}] {f.title}  ({loc})")

    print("\n" + "=" * 60)
    recall_pct = (total_matched / total_expected * 100) if total_expected else 0.0
    print(
        f"TOTAL  expected={total_expected}  matched={total_matched} "
        f"({recall_pct:.0f}% recall)  extra={total_extra}"
    )
    print("Note: 'extra' on the clean case = false positives; elsewhere may be valid.")


def main() -> None:
    logging.basicConfig(level="WARNING")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
