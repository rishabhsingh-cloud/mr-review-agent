"""Code chunking for RAG.

Primary strategy: **tree-sitter semantic chunking** — parse the file and pack
whole syntactic units (functions, classes, methods, import blocks) into
token-bounded chunks, so a chunk is a coherent unit rather than an arbitrary
line window. Oversized units recurse into their children; a too-big leaf is
line-split as a last resort.

Fallback: if tree-sitter (or a language's grammar) isn't available, or parsing
fails, we fall back to the original **token-aware line-window** chunker. This
keeps the app working everywhere (e.g. environments without the grammar wheels).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

logger = logging.getLogger(__name__)

# Token counting: prefer tiktoken, fall back to a cheap heuristic.
try:  # pragma: no cover - depends on environment
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except Exception:  # pragma: no cover

    def count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


# Our language label -> tree-sitter-language-pack grammar name. Only "real code"
# languages benefit from semantic chunking; others use the line fallback.
_LANG_TO_TS: dict[str, str] = {
    "python": "python", "javascript": "javascript", "typescript": "typescript",
    "go": "go", "java": "java", "ruby": "ruby", "php": "php", "rust": "rust",
    "c": "c", "cpp": "cpp", "csharp": "csharp", "kotlin": "kotlin",
    "scala": "scala", "swift": "swift",
}

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


# ─────────────────────────── tree-sitter path ───────────────────────────


@lru_cache(maxsize=None)
def _get_parser(language: str | None):
    """Return a tree-sitter parser for a language, or None if unavailable."""
    ts_name = _LANG_TO_TS.get(language or "")
    if not ts_name:
        return None
    try:
        from tree_sitter_language_pack import get_parser

        return get_parser(ts_name)
    except Exception:  # pragma: no cover - missing package/grammar
        return None


def _mk_chunk(source: bytes, start_byte: int, end_byte: int, start_row: int, end_row: int) -> Chunk:
    text = source[start_byte:end_byte].decode("utf-8", "ignore")
    return Chunk(
        content=text,
        start_line=start_row + 1,
        end_line=end_row + 1,
        token_count=count_tokens(text),
        symbols=extract_symbols(text.splitlines()),
    )


def _split_leaf_text(text: str, base_row: int, max_tokens: int) -> list[Chunk]:
    """Last resort: a single leaf node bigger than the budget → line-split it."""
    lines = text.split("\n")
    out: list[Chunk] = []
    win: list[str] = []
    tok = 0
    start_i = 0
    for i, line in enumerate(lines):
        lt = count_tokens(line) + 1
        if win and tok + lt > max_tokens:
            body = "\n".join(win)
            out.append(Chunk(body, base_row + start_i + 1, base_row + i, count_tokens(body),
                             extract_symbols(win)))
            win, tok, start_i = [], 0, i
        win.append(line)
        tok += lt
    if win:
        body = "\n".join(win)
        out.append(Chunk(body, base_row + start_i + 1, base_row + len(lines), count_tokens(body),
                         extract_symbols(win)))
    return out


def _pack(source: bytes, nodes: list, max_tokens: int) -> list[Chunk]:
    """Greedily pack sibling AST nodes into <= max_tokens chunks; recurse into
    oversized nodes so big classes split at method boundaries."""
    out: list[Chunk] = []
    cur: list = []
    cur_tok = 0

    def flush() -> None:
        nonlocal cur, cur_tok
        if cur:
            out.append(_mk_chunk(source, cur[0].start_byte, cur[-1].end_byte,
                                 cur[0].start_point[0], cur[-1].end_point[0]))
            cur, cur_tok = [], 0

    for node in nodes:
        text = source[node.start_byte:node.end_byte].decode("utf-8", "ignore")
        if not text.strip():
            continue
        tok = count_tokens(text)
        if tok > max_tokens:
            flush()
            children = [c for c in node.children if c.end_byte > c.start_byte]
            if children:
                out.extend(_pack(source, children, max_tokens))
            else:
                out.extend(_split_leaf_text(text, node.start_point[0], max_tokens))
        elif cur and cur_tok + tok > max_tokens:
            flush()
            cur, cur_tok = [node], tok
        else:
            cur.append(node)
            cur_tok += tok
    flush()
    return out


def _chunk_semantic(content: str, parser, max_tokens: int) -> list[Chunk]:
    source = content.encode("utf-8")
    tree = parser.parse(source)
    top = [c for c in tree.root_node.children if c.end_byte > c.start_byte]
    return _pack(source, top, max_tokens)


# ─────────────────────────── line-window fallback ───────────────────────────


def _chunk_by_lines(content: str, *, max_tokens: int, overlap_lines: int) -> list[Chunk]:
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
            line_tok = count_tokens(lines[j]) + 1
            if tok + line_tok > max_tokens and j > i:
                break
            tok += line_tok
            j += 1
        window = lines[i:j]
        chunks.append(Chunk("\n".join(window), i + 1, j, tok, extract_symbols(window)))
        if j >= n:
            break
        i = max(j - overlap_lines, i + 1)
    return chunks


# ─────────────────────────── public API ───────────────────────────


def chunk_code(
    content: str,
    *,
    max_tokens: int = 400,
    overlap_lines: int = 8,
    language: str | None = None,
) -> list[Chunk]:
    """Chunk source. Uses tree-sitter semantic chunking when a grammar is
    available for ``language``; otherwise falls back to line-window chunking."""
    parser = _get_parser(language)
    if parser is not None:
        try:
            chunks = _chunk_semantic(content, parser, max_tokens)
            if chunks:
                return chunks
        except Exception:  # pragma: no cover - defensive
            logger.warning("semantic chunking failed for language=%s; using line fallback", language)
    return _chunk_by_lines(content, max_tokens=max_tokens, overlap_lines=overlap_lines)
