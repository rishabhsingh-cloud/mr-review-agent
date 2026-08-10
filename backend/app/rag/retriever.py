"""Hybrid retrieval: DiskANN vector search + full-text, fused via RRF.

Vector search finds semantically similar chunks; full-text finds exact
identifiers/function names. Reciprocal rank fusion merges the two ranked lists
so a chunk that scores well on either signal surfaces.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import CodeChunk
from app.llm.embeddings import Embedder


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[int]], k: int = 60
) -> list[int]:
    """Fuse ranked id lists. Higher fused score = appears high in more lists."""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return [item_id for item_id, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


async def _vector_ids(
    session: AsyncSession, repo_id: int, query_vec: list[float], limit: int
) -> list[int]:
    stmt = (
        select(CodeChunk.id)
        .where(CodeChunk.repository_id == repo_id)
        .order_by(CodeChunk.embedding.cosine_distance(query_vec))
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def _fulltext_ids(
    session: AsyncSession, repo_id: int, query_text: str, limit: int
) -> list[int]:
    tsquery = func.websearch_to_tsquery("english", query_text)
    stmt = (
        select(CodeChunk.id)
        .where(CodeChunk.repository_id == repo_id)
        .where(CodeChunk.content_tsv.op("@@")(tsquery))
        .order_by(func.ts_rank(CodeChunk.content_tsv, tsquery).desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def hybrid_search(
    session: AsyncSession,
    repo_id: int,
    query_text: str,
    *,
    query_vec: list[float] | None = None,
    top_k: int | None = None,
    pool: int | None = None,
) -> list[CodeChunk]:
    """Return the top-k most relevant chunks for a query in a repository.

    If ``query_vec`` is omitted, the query text is embedded on the fly.
    """
    settings = get_settings()
    top_k = top_k or settings.rag_top_k
    pool = pool or settings.rag_candidate_pool

    if query_vec is None:
        result = await Embedder().embed([query_text])
        query_vec = result.vectors[0] if result.vectors else None
    if query_vec is None:
        return []

    vec_ids = await _vector_ids(session, repo_id, query_vec, pool)
    fts_ids = await _fulltext_ids(session, repo_id, query_text, pool)

    fused = reciprocal_rank_fusion([vec_ids, fts_ids])[:top_k]
    if not fused:
        return []

    rows = (
        await session.execute(select(CodeChunk).where(CodeChunk.id.in_(fused)))
    ).scalars().all()
    by_id = {r.id: r for r in rows}
    return [by_id[i] for i in fused if i in by_id]
