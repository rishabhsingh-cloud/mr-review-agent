"""OpenAI-compatible implementation of LLMProvider.

Works with the OpenAI API and any OpenAI-compatible server (vLLM, Ollama, etc.)
via a configurable base_url. Structured output uses JSON mode plus a schema
instruction in the system prompt, which is the most portable approach across
local servers that do not implement the json_schema response format.
"""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from app.llm.pricing import estimate_cost
from app.llm.provider import (
    LLMProvider,
    LLMResponse,
    StructuredResult,
    schema_instruction,
)
from app.reliability import with_retry


def _is_reasoning(model: str) -> bool:
    """Reasoning models (o-series, GPT-5 non-chat) reject `temperature` and use
    `max_completion_tokens`."""
    m = model.lower()
    if m.startswith(("o1", "o3", "o4")):
        return True
    if m.startswith("gpt-5") and "chat" not in m:
        return True
    return False


def _token_and_temp(model: str, max_tokens: int, temperature: float) -> dict:
    """Build the token/temperature kwargs appropriate for this model.

    `max_completion_tokens` is the modern param (accepted by gpt-4o too);
    `temperature` is omitted for reasoning models that only allow the default.
    """
    kwargs: dict = {"max_completion_tokens": max_tokens}
    if not _is_reasoning(model):
        kwargs["temperature"] = temperature
    return kwargs


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str | None = None):
        kwargs: dict[str, Any] = {"api_key": api_key or "missing"}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)

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
        resp = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **_token_and_temp(model, max_tokens, temperature),
        )
        text = resp.choices[0].message.content or ""
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
        resp = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": f"{system}\n\n{schema_instruction(schema)}"},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            **_token_and_temp(model, max_tokens, temperature),
        )
        content = resp.choices[0].message.content or "{}"
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = {}
        return StructuredResult(data=data, response=self._to_response(content, model, resp))

    def _to_response(self, text: str, model: str, resp: Any) -> LLMResponse:
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        return LLMResponse(
            text=text,
            model=model,
            provider=self.name,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=estimate_cost(model, in_tok, out_tok),
            raw=resp,
        )
