"""Run a specialist agent: build the prompt, call the provider for structured
findings, and parse them into typed objects."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.agents.schemas import Finding, FindingsResponse
from app.agents.specialists import Specialist
from app.config import get_settings
from app.llm.provider import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)

_SCHEMA = FindingsResponse.model_json_schema()


@dataclass
class AgentResult:
    agent_type: str
    findings: list[Finding] = field(default_factory=list)
    response: LLMResponse | None = None
    error: str | None = None
    latency_ms: int = 0
    target: str | None = None  # file path this agent reviewed (per-file fan-out)


def build_user_prompt(diff_text: str, context_text: str) -> str:
    return (
        "## Merge Request Diff\n"
        f"```diff\n{diff_text}\n```\n\n"
        "## Relevant Codebase Context (retrieved)\n"
        f"{context_text or '(no additional context retrieved)'}\n"
    )


async def run_agent(
    provider: LLMProvider,
    specialist: Specialist,
    *,
    diff_text: str,
    context_text: str,
    model: str,
    max_tokens: int,
    temperature: float,
) -> AgentResult:
    """Run one specialist and return its findings (never raises)."""
    user = build_user_prompt(diff_text, context_text)
    timeout = get_settings().agent_timeout_seconds
    start = time.perf_counter()
    try:
        async with asyncio.timeout(timeout):
            result = await provider.complete_structured(
                system=specialist.system_prompt,
                user=user,
                model=model,
                schema=_SCHEMA,
                max_tokens=max_tokens,
                temperature=temperature,
            )
    except Exception as exc:  # noqa: BLE001 — one agent failing must not kill the review
        logger.exception("Agent %s failed", specialist.agent_type)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return AgentResult(
            agent_type=specialist.agent_type, error=str(exc), latency_ms=latency_ms
        )
    latency_ms = int((time.perf_counter() - start) * 1000)

    try:
        parsed = FindingsResponse.model_validate(result.data)
    except Exception:  # noqa: BLE001 — tolerate malformed output
        logger.warning("Agent %s returned unparseable findings", specialist.agent_type)
        parsed = FindingsResponse(findings=[])

    return AgentResult(
        agent_type=specialist.agent_type,
        findings=parsed.findings,
        response=result.response,
        latency_ms=latency_ms,
    )
