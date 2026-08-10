"""Tests for unified-diff parsing and inline-comment position mapping."""

from __future__ import annotations

from app.gitlab.diff import (
    DiffRefs,
    parse_mr_changes,
    parse_unified_diff,
    position_for_new_line,
)

SAMPLE_DIFF = """@@ -1,4 +1,5 @@
 def foo():
-    return 1
+    return 2
+    # added comment
 x = 5
 y = 6
"""

REFS = DiffRefs(base_sha="base", start_sha="start", head_sha="head")


def test_parse_unified_diff_line_types():
    lines = parse_unified_diff(SAMPLE_DIFF)
    types = [(dl.type, dl.old_line, dl.new_line) for dl in lines]
    assert types == [
        ("context", 1, 1),   # def foo():
        ("removed", 2, None),  # return 1
        ("added", None, 2),  # return 2
        ("added", None, 3),  # # added comment
        ("context", 3, 4),   # x = 5
        ("context", 4, 5),   # y = 6
    ]


def test_position_for_added_line():
    _refs, files = _parsed_changes()
    pos = position_for_new_line(files[0], REFS, new_line=2)
    assert pos is not None
    assert pos["new_line"] == 2
    assert "old_line" not in pos  # added line → new_line only
    assert pos["new_path"] == "app/foo.py"
    assert pos["head_sha"] == "head"


def test_position_for_context_line_has_both():
    _refs, files = _parsed_changes()
    pos = position_for_new_line(files[0], REFS, new_line=4)  # 'x = 5' context line
    assert pos is not None
    assert pos["new_line"] == 4
    assert pos["old_line"] == 3  # context → both lines set


def test_line_not_in_diff_is_unmappable():
    _refs, files = _parsed_changes()
    assert position_for_new_line(files[0], REFS, new_line=999) is None


def test_parse_mr_changes_shape():
    refs, files = _parsed_changes()
    assert refs.base_sha == "base"
    assert len(files) == 1
    assert files[0].new_path == "app/foo.py"


def _parsed_changes():
    changes = {
        "diff_refs": {"base_sha": "base", "start_sha": "start", "head_sha": "head"},
        "changes": [
            {
                "old_path": "app/foo.py",
                "new_path": "app/foo.py",
                "new_file": False,
                "deleted_file": False,
                "renamed_file": False,
                "diff": SAMPLE_DIFF,
            }
        ],
    }
    return parse_mr_changes(changes)
