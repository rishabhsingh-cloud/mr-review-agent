"""Tests for the retry decorator (transient vs non-transient)."""

from __future__ import annotations

import pytest

from app.reliability import with_retry


async def test_retries_transient_then_succeeds():
    state = {"n": 0}

    @with_retry
    async def flaky() -> str:
        state["n"] += 1
        if state["n"] < 3:
            raise TimeoutError("transient")
        return "ok"

    assert await flaky() == "ok"
    assert state["n"] == 3  # retried until success


async def test_non_transient_raises_immediately():
    state = {"n": 0}

    @with_retry
    async def bad() -> None:
        state["n"] += 1
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        await bad()
    assert state["n"] == 1  # no retries on non-transient errors
