"""LLM provider interface and shared response types.

All providers return an ``LLMResponse`` carrying token counts and an estimated
USD cost so the telemetry/cost lanes can record every call uniformly.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw: Any = field(default=None, repr=False)


@dataclass
class StructuredResult:
    data: dict[str, Any]
    response: LLMResponse


def schema_instruction(schema: dict[str, Any]) -> str:
    """Prompt fragment telling a model to emit JSON matching a schema.

    Used by providers that rely on JSON mode (OpenAI-compatible/local) rather
    than native tool-calling.
    """
    return (
        "You must respond with a single JSON object that conforms exactly to "
        "this JSON Schema. Do not include any prose, markdown, or code fences.\n\n"
        f"JSON Schema:\n{json.dumps(schema, indent=2)}"
    )


class LLMProvider(ABC):
    """Common surface for text completion and schema-constrained output."""

    name: str

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Return a free-text completion."""

    @abstractmethod
    async def complete_structured(
        self,
        *,
        system: str,
        user: str,
        model: str,
        schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
    ) -> StructuredResult:
        """Return output constrained to ``schema`` (parsed into a dict)."""
