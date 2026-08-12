"""Deterministic tests for the semantic chunker's node-packing algorithm.

Uses fake AST nodes + a word-count tokenizer so the greedy packing, recursion
into oversized nodes, and leaf-splitting are validated without needing
tree-sitter installed (works on any Python version).
"""

from __future__ import annotations

from app.rag import chunker


class FakeNode:
    def __init__(self, start_byte, end_byte, start_row, end_row, children=()):
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.start_point = (start_row, 0)
        self.end_point = (end_row, 0)
        self.children = list(children)


def _word_tokens(monkeypatch):
    monkeypatch.setattr(chunker, "count_tokens", lambda t: len(t.split()))


def test_pack_groups_small_siblings(monkeypatch):
    _word_tokens(monkeypatch)
    source = b"aa bb\ncc dd\nee ff"  # three 2-word segments
    nodes = [FakeNode(0, 5, 0, 0), FakeNode(6, 11, 1, 1), FakeNode(12, 17, 2, 2)]
    chunks = chunker._pack(source, nodes, max_tokens=4)
    # budget 4 → n1(2)+n2(2) fill one chunk, n3 starts the next
    assert len(chunks) == 2
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 2)
    assert chunks[1].start_line == 3


def test_pack_recurses_into_oversized_node(monkeypatch):
    _word_tokens(monkeypatch)
    source = b"aa bb cc dd"
    child1 = FakeNode(0, 5, 0, 0)   # "aa bb" (2)
    child2 = FakeNode(6, 11, 0, 0)  # "cc dd" (2)
    big = FakeNode(0, 11, 0, 0, children=[child1, child2])  # 4 tokens > budget
    chunks = chunker._pack(source, [big], max_tokens=3)
    # oversized parent → recurse; children 2+2 > 3 → split into two chunks
    assert len(chunks) == 2


def test_leaf_too_big_is_line_split(monkeypatch):
    _word_tokens(monkeypatch)
    source = b"a a a\nb b b"  # 6 tokens, no children → line-split
    leaf = FakeNode(0, 11, 0, 1)
    chunks = chunker._pack(source, [leaf], max_tokens=3)
    assert len(chunks) == 2
    assert (chunks[0].start_line, chunks[1].start_line) == (1, 2)


def test_single_chunk_when_budget_large(monkeypatch):
    _word_tokens(monkeypatch)
    source = b"aa bb\ncc dd"
    nodes = [FakeNode(0, 5, 0, 0), FakeNode(6, 11, 1, 1)]
    chunks = chunker._pack(source, nodes, max_tokens=100)
    assert len(chunks) == 1
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 2)
