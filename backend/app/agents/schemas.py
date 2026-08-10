"""Structured output types for specialist agents."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class Finding(BaseModel):
    title: str = Field(description="Short, specific description of the issue")
    file_path: str | None = Field(default=None, description="Path from the diff")
    line_start: int | None = Field(default=None, description="New-file line number")
    line_end: int | None = None
    severity: Severity = Severity.medium
    category: str | None = Field(default=None, description="e.g. sql-injection, missing-test")
    rationale: str = Field(default="", description="Why this is a problem")
    suggestion: str | None = Field(default=None, description="Concrete fix, if any")
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="0-1 certainty this is a real issue"
    )


class FindingsResponse(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
