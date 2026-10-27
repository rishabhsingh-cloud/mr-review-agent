"""Tests for chunking, symbol extraction, and rank fusion (all pure)."""

from __future__ import annotations

from app.rag.chunker import chunk_code, extract_symbols
from app.rag.retriever import fulltext_query, reciprocal_rank_fusion

SAMPLE = """import os


def alpha(x):
    return x + 1


class Beta:
    def method(self):
        return 2


def gamma():
    return 3
"""


def test_chunk_covers_all_lines_single_chunk():
    chunks = chunk_code(SAMPLE, max_tokens=1000, overlap_lines=2)
    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == len(SAMPLE.splitlines())
    assert chunks[0].token_count > 0


def test_chunk_splits_with_small_budget():
    chunks = chunk_code(SAMPLE, max_tokens=10, overlap_lines=1)
    assert len(chunks) > 1
    # Chunks are ordered and line ranges are sane.
    for c in chunks:
        assert c.start_line <= c.end_line
    assert chunks[0].start_line == 1


def test_extract_symbols():
    syms = extract_symbols(SAMPLE.splitlines())
    assert "alpha" in syms
    assert "Beta" in syms
    assert "gamma" in syms


def test_rrf_orders_by_agreement():
    # id 2 appears high in both lists -> should rank first.
    vec = [1, 2, 3]
    fts = [2, 4, 1]
    fused = reciprocal_rank_fusion([vec, fts])
    assert fused[0] == 2
    assert set(fused) == {1, 2, 3, 4}


def test_rrf_empty():
    assert reciprocal_rank_fusion([[], []]) == []


def test_fulltext_query_flat_or_of_identifiers():
    diff = 'b/pages/list_tab.py\n+def _list_request(self, action):\n+    return "on-off" - x'
    q = fulltext_query(diff)
    assert q == "pages or list_tab or _list_request or action or off"
    assert '"' not in q and "-" not in q


def test_fulltext_query_caps_terms_and_handles_empty():
    many = " ".join(f"name{i}" for i in range(100))
    assert len(fulltext_query(many, max_terms=5).split(" or ")) == 5
    assert fulltext_query("+ - { } ()") == ""
