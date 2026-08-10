"""Parse GitLab MR diffs and map findings to inline-comment positions.

GitLab pins an inline discussion to a diff line via a ``position`` object that
needs the MR's diff refs (base/start/head SHA), the file paths, and the correct
``old_line``/``new_line``. This module turns a unified diff into a line map and
builds those positions.

Position line rules (GitLab):
  - added line   → new_line only
  - removed line → old_line only
  - context line → both old_line and new_line
A finding whose line is not present in the diff cannot be pinned inline; callers
should fall back to a file-level or summary note.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Matches a hunk header like: @@ -12,7 +12,9 @@ optional section heading
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Lines that appear in a raw git diff but are not content lines.
_HEADER_PREFIXES = (
    "--- ",
    "+++ ",
    "diff --git",
    "index ",
    "new file mode",
    "deleted file mode",
    "old mode",
    "new mode",
    "rename from",
    "rename to",
    "similarity index",
    "Binary files",
)

LineType = str  # "added" | "removed" | "context"


@dataclass(frozen=True)
class DiffLine:
    old_line: int | None
    new_line: int | None
    type: LineType


@dataclass(frozen=True)
class DiffRefs:
    base_sha: str
    start_sha: str
    head_sha: str


@dataclass
class FileDiff:
    old_path: str
    new_path: str
    new_file: bool = False
    deleted_file: bool = False
    renamed_file: bool = False
    lines: list[DiffLine] = field(default_factory=list)


def parse_unified_diff(diff_text: str) -> list[DiffLine]:
    """Turn a unified diff string into an ordered list of ``DiffLine``."""
    result: list[DiffLine] = []
    old_ln = new_ln = 0
    in_hunk = False

    for raw in diff_text.splitlines():
        hunk = _HUNK_RE.match(raw)
        if hunk:
            old_ln = int(hunk.group(1))
            new_ln = int(hunk.group(2))
            in_hunk = True
            continue
        if not in_hunk or raw.startswith(_HEADER_PREFIXES):
            continue
        if raw == "":
            # Blank context line (no leading space is possible in some diffs).
            result.append(DiffLine(old_ln, new_ln, "context"))
            old_ln += 1
            new_ln += 1
            continue

        tag = raw[0]
        if tag == "+":
            result.append(DiffLine(None, new_ln, "added"))
            new_ln += 1
        elif tag == "-":
            result.append(DiffLine(old_ln, None, "removed"))
            old_ln += 1
        elif tag == " ":
            result.append(DiffLine(old_ln, new_ln, "context"))
            old_ln += 1
            new_ln += 1
        elif tag == "\\":
            # "\ No newline at end of file" — not a content line.
            continue
    return result


def parse_mr_changes(changes: dict[str, Any]) -> tuple[DiffRefs, list[FileDiff]]:
    """Split the dict returned by ``mr.changes()`` into refs + parsed file diffs."""
    refs_d = changes["diff_refs"]
    refs = DiffRefs(
        base_sha=refs_d["base_sha"],
        start_sha=refs_d["start_sha"],
        head_sha=refs_d["head_sha"],
    )
    files = [
        FileDiff(
            old_path=ch["old_path"],
            new_path=ch["new_path"],
            new_file=ch.get("new_file", False),
            deleted_file=ch.get("deleted_file", False),
            renamed_file=ch.get("renamed_file", False),
            lines=parse_unified_diff(ch.get("diff", "")),
        )
        for ch in changes.get("changes", [])
    ]
    return refs, files


def build_position(
    file_diff: FileDiff,
    refs: DiffRefs,
    *,
    new_line: int | None = None,
    old_line: int | None = None,
) -> dict[str, Any]:
    """Assemble a GitLab text position object."""
    pos: dict[str, Any] = {
        "base_sha": refs.base_sha,
        "start_sha": refs.start_sha,
        "head_sha": refs.head_sha,
        "position_type": "text",
        "new_path": file_diff.new_path,
        "old_path": file_diff.old_path,
    }
    if new_line is not None:
        pos["new_line"] = new_line
    if old_line is not None:
        pos["old_line"] = old_line
    return pos


def position_for_new_line(
    file_diff: FileDiff, refs: DiffRefs, new_line: int
) -> dict[str, Any] | None:
    """Position for a finding at ``new_line`` (post-change line number).

    Returns None if that line is not part of the diff (can't be pinned inline).
    """
    for dl in file_diff.lines:
        if dl.new_line == new_line:
            if dl.type == "added":
                return build_position(file_diff, refs, new_line=new_line)
            if dl.type == "context":
                return build_position(
                    file_diff, refs, new_line=new_line, old_line=dl.old_line
                )
    return None


def find_file_diff(file_diffs: list[FileDiff], path: str) -> FileDiff | None:
    """Locate a file diff by its new (or old) path."""
    for fd in file_diffs:
        if fd.new_path == path or fd.old_path == path:
            return fd
    return None
