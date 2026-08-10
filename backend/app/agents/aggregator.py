"""Merge findings across agents and decide the confidence-weighted gate.

Dedup: findings on the same file+line are collapsed, keeping the highest-
confidence one and noting which agents agreed. The gate then routes the review:
  - any CRITICAL finding            -> escalate
  - any finding below the threshold -> human-in-the-loop review
  - otherwise                       -> auto-post
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.base import AgentResult
from app.agents.schemas import Finding, Severity


@dataclass
class AggregatedFinding:
    finding: Finding
    agents: list[str] = field(default_factory=list)

    @property
    def agreement(self) -> int:
        return len(self.agents)


def _dedup_key(f: Finding) -> tuple:
    return (f.file_path or "", f.line_start or 0, (f.title or "").strip().lower()[:60])


def aggregate(agent_results: list[AgentResult]) -> list[AggregatedFinding]:
    """Collapse duplicate findings; keep highest confidence, record agreement."""
    merged: dict[tuple, AggregatedFinding] = {}
    for result in agent_results:
        for finding in result.findings:
            key = _dedup_key(finding)
            existing = merged.get(key)
            if existing is None:
                merged[key] = AggregatedFinding(finding=finding, agents=[result.agent_type])
            else:
                if result.agent_type not in existing.agents:
                    existing.agents.append(result.agent_type)
                if finding.confidence > existing.finding.confidence:
                    existing.finding = finding

    # Sort by severity then confidence (most important first).
    severity_rank = {
        Severity.critical: 0, Severity.high: 1, Severity.medium: 2,
        Severity.low: 3, Severity.info: 4,
    }
    return sorted(
        merged.values(),
        key=lambda a: (severity_rank.get(a.finding.severity, 5), -a.finding.confidence),
    )


# ── confidence-weighted gate ──

AUTO_POST = "auto_post"
HITL = "hitl"
ESCALATE = "escalate"


@dataclass
class GateDecision:
    action: str  # AUTO_POST | HITL | ESCALATE
    reason: str
    critical: int = 0
    low_confidence: int = 0
    total: int = 0


def decide_gate(
    aggregated: list[AggregatedFinding], *, min_confidence: float
) -> GateDecision:
    total = len(aggregated)
    critical = sum(1 for a in aggregated if a.finding.severity == Severity.critical)
    low_conf = sum(1 for a in aggregated if a.finding.confidence < min_confidence)

    if critical:
        action, reason = ESCALATE, f"{critical} critical finding(s) require attention"
    elif low_conf:
        action, reason = HITL, f"{low_conf} finding(s) below confidence {min_confidence}"
    else:
        action, reason = AUTO_POST, "all findings meet the confidence threshold"

    return GateDecision(
        action=action, reason=reason,
        critical=critical, low_confidence=low_conf, total=total,
    )
