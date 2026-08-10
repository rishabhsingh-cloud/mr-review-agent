"""Retry helpers for transient LLM / embedding / network failures.

Wraps calls with exponential-backoff retries on transient error types from the
OpenAI and Anthropic SDKs (rate limits, timeouts, connection errors, 5xx). Non-
transient errors (e.g. a 400 bad request) are re-raised immediately.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

_transient: list[type[BaseException]] = [ConnectionError, TimeoutError]

try:  # pragma: no cover - depends on installed SDK
    import openai

    _transient += [
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.RateLimitError,
        openai.InternalServerError,
    ]
except Exception:  # pragma: no cover
    pass

try:  # pragma: no cover
    import anthropic

    _transient += [
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        anthropic.RateLimitError,
        anthropic.InternalServerError,
    ]
except Exception:  # pragma: no cover
    pass

TRANSIENT_ERRORS = tuple(_transient)

F = TypeVar("F", bound=Callable)


def with_retry(fn: F) -> F:
    """Decorator: retry a coroutine on transient errors with backoff."""
    attempts = get_settings().llm_retry_attempts
    return retry(  # type: ignore[return-value]
        reraise=True,
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=0.3, max=8),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
    )(fn)
