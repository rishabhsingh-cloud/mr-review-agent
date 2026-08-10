"""Token-aware code chunking.

A pragmatic, language-agnostic chunker: it groups source lines into windows that
fit a token budget, with a small line overlap so context isn't cut mid-idea, and
best-effort symbol extraction (def/class/func names) for keyword retrieval.

This is intentionally dependency-light. A tree-sitter semantic chunker can be
swapped in behind the same `chunk_code` signature later without touching callers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Token counting: prefer tiktoken, fall back to a cheap heuristic if the encoding
# can't be loaded (e.g. offline environment).
try:  # pragma: no cover - depends on environment
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except Exception:  # pragma: no cover

    def count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


# Best-effort symbol names across common languages.
_SYMBOL_RE = re.compile(
    r"^\s*(?:def|class|func|function|fn|interface|type|struct)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"|^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"|^\s*(?:public|private|protected|static|\s)*[A-Za-z_<>\[\]]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("
)


@dataclass
class Chunk:
    content: str
    start_line: int  # 1-based, inclusive
    end_line: int    # 1-based, inclusive
    token_count: int
    symbols: list[str] = field(default_factory=list)


def extract_symbols(lines: list[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        m = _SYMBOL_RE.match(line)
        if m:
            name = next((g for g in m.groups() if g), None)
            if name and name not in found:
                found.append(name)
    return found


def chunk_code(
    content: str,
    *,
    max_tokens: int = 400,
    overlap_lines: int = 8,
) -> list[Chunk]:
    """Split source text into overlapping, token-bounded line windows."""
    lines = content.splitlines()
    n = len(lines)
    if n == 0:
        return []

    chunks: list[Chunk] = []
    i = 0
    while i < n:
        tok = 0
        j = i
        while j < n:
            line_tok = count_tokens(lines[j]) + 1  # +1 approximates the newline
            if tok + line_tok > max_tokens and j > i:
                break
            tok += line_tok
            j += 1

        window = lines[i:j]
        chunks.append(
            Chunk(
                content="\n".join(window),
                start_line=i + 1,
                end_line=j,
                token_count=tok,
                symbols=extract_symbols(window),
            )
        )
        if j >= n:
            break
        # Step forward, keeping a small overlap for context continuity.
        i = max(j - overlap_lines, i + 1)
    return chunks
