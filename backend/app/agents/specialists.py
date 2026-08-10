"""Registry of specialist agents and scope selection."""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.prompts import (
    DOCS_PROMPT,
    QUALITY_PROMPT,
    SECURITY_PROMPT,
    TESTS_PROMPT,
)


@dataclass(frozen=True)
class Specialist:
    agent_type: str
    system_prompt: str


SPECIALISTS: dict[str, Specialist] = {
    "security": Specialist("security", SECURITY_PROMPT),
    "quality": Specialist("quality", QUALITY_PROMPT),
    "tests": Specialist("tests", TESTS_PROMPT),
    "docs": Specialist("docs", DOCS_PROMPT),
}


def agents_for_scope(scope: str) -> list[Specialist]:
    """Return the specialists to run for a command scope.

    'all' (or anything unrecognized) runs the full panel; a specific scope runs
    just that one agent (from `/review security`, etc.).
    """
    if scope in SPECIALISTS:
        return [SPECIALISTS[scope]]
    return list(SPECIALISTS.values())
