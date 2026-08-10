"""Render aggregated findings into MR comment bodies and a summary note."""

from __future__ import annotations

from app.agents.aggregator import AggregatedFinding, GateDecision
from app.agents.schemas import Severity
from app.gitlab.publish import InlineComment

_SEVERITY_EMOJI = {
    Severity.critical: "🔴",
    Severity.high: "🟠",
    Severity.medium: "🟡",
    Severity.low: "🔵",
    Severity.info: "⚪",
}
_SEVERITY_EMOJI_BY_STR = {s.value: e for s, e in _SEVERITY_EMOJI.items()}


def format_comment(agg: AggregatedFinding) -> str:
    f = agg.finding
    emoji = _SEVERITY_EMOJI.get(f.severity, "•")
    agents = ", ".join(agg.agents)
    body = (
        f"{emoji} **{f.severity.value.upper()}: {f.title}**  \n"
        f"_{agents} · confidence {f.confidence:.2f}_\n\n"
        f"{f.rationale}"
    )
    if f.suggestion:
        body += f"\n\n**Suggestion:** {f.suggestion}"
    return body


def format_finding_record(rec) -> str:
    """Render a stored FindingRecord (used when publishing on HITL approval)."""
    emoji = _SEVERITY_EMOJI_BY_STR.get(rec.severity, "•")
    body = (
        f"{emoji} **{rec.severity.upper()}: {rec.title}**  \n"
        f"_{rec.agent_type} · confidence {rec.confidence:.2f}_\n\n"
        f"{rec.rationale or ''}"
    )
    return body


def to_inline_comments(aggregated: list[AggregatedFinding]) -> list[InlineComment]:
    comments: list[InlineComment] = []
    for agg in aggregated:
        f = agg.finding
        if f.file_path and f.line_start:
            comments.append(
                InlineComment(file_path=f.file_path, new_line=f.line_start, body=format_comment(agg))
            )
    return comments


def build_summary(decision: GateDecision, aggregated: list[AggregatedFinding], scope: str) -> str:
    counts: dict[str, int] = {}
    for agg in aggregated:
        sev = agg.finding.severity.value
        counts[sev] = counts.get(sev, 0) + 1
    breakdown = ", ".join(f"{n} {sev}" for sev, n in counts.items()) or "no issues"

    lines = [
        f"## 🤖 AI Merge Request Review — scope: `{scope}`",
        "",
        f"**{decision.total} finding(s):** {breakdown}",
        f"**Gate:** `{decision.action}` — {decision.reason}",
    ]
    if decision.action != "auto_post":
        lines.append("")
        lines.append("_Findings are held for human review and not yet posted inline._")
    return "\n".join(lines)
