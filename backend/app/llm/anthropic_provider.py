"""Anthropic implementation of LLMProvider.

Structured output uses native tool-calling with a forced tool choice, which is
the most reliable way to get schema-valid JSON from Claude.
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from app.llm.pricing import estimate_cost
from app.llm.provider import LLMProvider, LLMResponse, StructuredResult
from app.reliability import with_retry

_STRUCT_TOOL_NAME = "report"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str):
        # Client construction does not require a valid key; calls do.
        self._client = AsyncAnthropic(api_key=api_key or "missing")

    @with_retry
    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        resp = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return self._to_response(text, model, resp)

    @with_retry
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
        tool = {
            "name": _STRUCT_TOOL_NAME,
            "description": "Return the structured result for this task.",
            "input_schema": schema,
        }
        resp = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": _STRUCT_TOOL_NAME},
        )
        data: dict[str, Any] = {}
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _STRUCT_TOOL_NAME:
                data = dict(block.input)
                break
        return StructuredResult(data=data, response=self._to_response("", model, resp))

    def _to_response(self, text: str, model: str, resp: Any) -> LLMResponse:
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        return LLMResponse(
            text=text,
            model=model,
            provider=self.name,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=estimate_cost(model, in_tok, out_tok),
            raw=resp,
        )
