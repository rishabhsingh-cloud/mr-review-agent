"""Embeddings client (OpenAI text-embedding-3-large, 256-dim by default).

Isolated behind one class so swapping to a local embedder later is a single-file
change (as noted in the plan's privacy tradeoff).
"""

from __future__ import annotations

from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import get_settings
from app.llm.pricing import estimate_embedding_cost
from app.reliability import with_retry


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    tokens: int
    cost_usd: float
    model: str


class Embedder:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        dim: int | None = None,
    ):
        settings = get_settings()
        self.model = model or settings.embedding_model
        self.dim = dim or settings.embedding_dim
        self._client = AsyncOpenAI(api_key=(api_key or settings.openai_api_key) or "missing")

    @with_retry
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Embed a batch of texts. Returns vectors + token usage + cost."""
        if not texts:
            return EmbeddingResult(vectors=[], tokens=0, cost_usd=0.0, model=self.model)
        resp = await self._client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dim,
        )
        vectors = [d.embedding for d in resp.data]
        tokens = getattr(resp.usage, "total_tokens", 0) or 0
        return EmbeddingResult(
            vectors=vectors,
            tokens=tokens,
            cost_usd=estimate_embedding_cost(self.model, tokens),
            model=self.model,
        )
