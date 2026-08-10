"""Token → USD cost estimation.

Prices are USD per 1M tokens (input, output). These are approximate and meant
to be edited to match your contracted rates — the cost ledger and budget guard
read from here. Unknown models fall back to zero cost (and should be added).
"""

from __future__ import annotations

# model_id -> (input_per_million, output_per_million)
# NOTE: APPROXIMATE. Verify against your current OpenAI/Anthropic pricing and
# edit — these drive the cost ledger and the daily budget guard. Reasoning
# models also bill hidden "reasoning" tokens as output, so real spend runs
# higher than a naive prompt-size estimate.
_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # OpenAI GPT-4 class
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    # OpenAI GPT-5 / reasoning class (ESTIMATES — confirm before relying on them)
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5.5": (1.75, 14.0),
    "gpt-5.2-codex": (1.75, 14.0),
    "gpt-5.1-codex-max": (2.0, 16.0),
    "o3": (2.0, 8.0),
    "o4-mini": (1.1, 4.4),
}

# Embedding models: USD per 1M tokens (input only).
_EMBED_PRICES: dict[str, float] = {
    "text-embedding-3-large": 0.13,
    "text-embedding-3-small": 0.02,
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = _PRICES.get(model)
    if rates is None:
        return 0.0
    in_rate, out_rate = rates
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def estimate_embedding_cost(model: str, tokens: int) -> float:
    rate = _EMBED_PRICES.get(model, 0.0)
    return (tokens / 1_000_000) * rate
