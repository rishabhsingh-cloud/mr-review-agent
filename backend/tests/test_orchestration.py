"""Pure tests for diff/query building and comment rendering (no network/DB)."""

from __future__ import annotations

from app.agents.aggregator import AUTO_POST, HITL, aggregate, decide_gate
from app.agents.base import AgentResult
from app.agents.schemas import Finding, Severity
from types import SimpleNamespace

from app.orchestrator.context import build_diff_text, build_file_units, diff_query
from app.orchestrator.render import (
    build_summary,
    format_comment,
    format_finding_record,
    to_inline_comments,
)

CHANGES = {
    "diff_refs": {"base_sha": "a", "start_sha": "a", "head_sha": "b"},
    "changes": [
        {
            "old_path": "app/db.py",
            "new_path": "app/db.py",
            "diff": "@@ -1,1 +1,2 @@\n import os\n+SECRET = 'x'\n",
        }
    ],
}


def test_build_diff_text_has_headers_and_hunk():
    text = build_diff_text(CHANGES)
    assert "+++ b/app/db.py" in text
    assert "SECRET = 'x'" in text


def test_diff_query_extracts_added_lines():
    q = diff_query(build_diff_text(CHANGES))
    assert "SECRET = 'x'" in q


def test_build_file_units_splits_and_skips():
    changes = {
        "changes": [
            {"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1 +1 @@\n+x=1\n"},
            {"old_path": "yarn.lock", "new_path": "yarn.lock", "diff": "@@ +1 @@\n+dep\n"},
            {"old_path": "b.py", "new_path": "b.py", "deleted_file": True, "diff": "x"},
            {"old_path": "c.py", "new_path": "c.py", "diff": "   "},  # empty
        ]
    }
    units = build_file_units(changes)
    paths = [u["path"] for u in units]
    assert paths == ["a.py"]  # lock skipped, deleted skipped, empty skipped


def test_build_file_units_truncates_large_diff():
    big = "@@ -1 +1 @@\n" + "\n".join(f"+line{i}" for i in range(5000))
    changes = {"changes": [{"old_path": "big.py", "new_path": "big.py", "diff": big}]}
    units = build_file_units(changes, max_chars=500)
    assert len(units) == 1
    assert "truncated" in units[0]["diff_text"]


def _aggregated(**kw):
    return aggregate([AgentResult("security", [Finding(**kw)])])


def test_format_comment_contains_severity_and_agent():
    agg = _aggregated(title="Hardcoded secret", severity=Severity.critical,
                      file_path="app/db.py", line_start=2, confidence=0.95,
                      rationale="secret in code")
    body = format_comment(agg[0])
    assert "CRITICAL" in body
    assert "security" in body
    assert "secret in code" in body


def test_to_inline_comments_only_for_located_findings():
    with_line = _aggregated(title="a", file_path="x.py", line_start=3, confidence=0.9)
    no_line = _aggregated(title="b", confidence=0.9)
    assert len(to_inline_comments(with_line)) == 1
    assert len(to_inline_comments(no_line)) == 0


def test_build_summary_reflects_gate():
    agg = _aggregated(title="maybe", severity=Severity.low, confidence=0.4)
    decision = decide_gate(agg, min_confidence=0.6)
    summary = build_summary(decision, agg, "all")
    assert decision.action == HITL
    assert "held for human review" in summary


def test_format_finding_record():
    rec = SimpleNamespace(
        severity="critical", title="RCE", agent_type="security",
        confidence=0.95, rationale="unsafe eval",
    )
    body = format_finding_record(rec)
    assert "CRITICAL" in body
    assert "security" in body
    assert "unsafe eval" in body
