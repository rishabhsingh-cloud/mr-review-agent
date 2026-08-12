"""Semantic (tree-sitter) chunking tests.

Skipped automatically where tree-sitter/grammars aren't installed (the app falls
back to line-window chunking there). Runs in the Docker image (Python 3.12).
"""

from __future__ import annotations

import pytest

from app.rag.chunker import _get_parser, chunk_code

pytestmark = pytest.mark.skipif(
    _get_parser("python") is None, reason="tree-sitter not installed in this env"
)

PY = '''import os


def alpha():
    return 1


class Beta:
    def method(self):
        return 2


def gamma():
    return 3
'''


def test_semantic_parses_and_covers_file():
    chunks = chunk_code(PY, max_tokens=1000, language="python")
    assert chunks
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line >= 12
    # symbols get picked up from the code
    all_syms = {s for c in chunks for s in c.symbols}
    assert {"alpha", "Beta", "gamma"} & all_syms


def test_semantic_splits_with_small_budget():
    chunks = chunk_code(PY, max_tokens=8, language="python")
    assert len(chunks) > 1
    for c in chunks:
        assert c.start_line <= c.end_line
        assert c.content.strip()
