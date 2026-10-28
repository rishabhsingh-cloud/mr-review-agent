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


# Plain-English labels so readers don't need to know our internals.
_SEVERITY_LABEL = {
    "critical": "CRITICAL — must fix before merging",
    "high": "HIGH — should fix before merging",
    "medium": "MEDIUM — worth fixing",
    "low": "LOW — minor",
    "info": "NOTE — for your information",
}
_SCOPE_LABEL = {
    "all": "security, code quality, tests and docs",
    "security": "security only",
    "quality": "code quality only",
    "tests": "tests only",
    "docs": "docs only",
}


def _certainty(confidence: float) -> str:
    if confidence >= 0.85:
        return "very likely a real issue"
    if confidence >= 0.6:
        return "likely a real issue"
    return "might be an issue, please double-check"


def _render(emoji: str, severity: str, title: str, found_by: str, confidence: float,
            rationale: str, suggestion: str | None) -> str:
    label = _SEVERITY_LABEL.get(severity, severity.upper())
    body = (
        f"{emoji} **{title}**  \n"
        f"_{label} · {_certainty(confidence)} · found by: {found_by}_\n\n"
        f"{rationale}"
    )
    if suggestion:
        body += f"\n\n**How to fix:** {suggestion}"
    return body


def format_comment(agg: AggregatedFinding) -> str:
    f = agg.finding
    return _render(
        _SEVERITY_EMOJI.get(f.severity, "•"), f.severity.value, f.title,
        ", ".join(agg.agents), f.confidence, f.rationale, f.suggestion,
    )


def format_finding_record(rec) -> str:
    """Render a stored FindingRecord (used when publishing on HITL approval)."""
    return _render(
        _SEVERITY_EMOJI_BY_STR.get(rec.severity, "•"), rec.severity, rec.title,
        rec.agent_type, rec.confidence, rec.rationale or "", None,
    )


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

    lines = [
        "## 🤖 AI review of this merge request",
        "",
        f"Checked: {_SCOPE_LABEL.get(scope, scope)}.",
        "",
    ]
    if not aggregated:
        lines.append("✅ No problems found.")
    else:
        n = decision.total
        lines.append(f"Found **{n} {'issue' if n == 1 else 'issues'}** — see the comments on the code:")
        for sev, n in counts.items():
            lines.append(f"- {_SEVERITY_EMOJI_BY_STR.get(sev, '•')} {n} × {_SEVERITY_LABEL.get(sev, sev)}")
    if decision.action != "auto_post":
        lines.append("")
        lines.append(
            "_Some of these need a person to check them first, so they are "
            "held for human review and not posted on the code yet._"
        )
    return "\n".join(lines)
