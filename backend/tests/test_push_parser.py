"""Tests for push-event parsing → incremental reindex requests."""

from __future__ import annotations

from app.webhooks.parser import parse_push_event


def _push(ref="refs/heads/main", default_branch="main", after="abc123", commits=None):
    return {
        "object_kind": "push",
        "ref": ref,
        "after": after,
        "project": {"id": 42, "default_branch": default_branch},
        "commits": commits if commits is not None else [],
    }


def test_push_on_default_branch_collects_changes():
    payload = _push(commits=[
        {"added": ["a.py"], "modified": ["b.py"], "removed": []},
        {"added": [], "modified": ["b.py"], "removed": ["c.py"]},
    ])
    req = parse_push_event(payload)
    assert req is not None
    assert req.project_id == 42
    assert req.after_sha == "abc123"
    assert set(req.changed) == {"a.py", "b.py"}
    assert req.removed == ["c.py"]


def test_non_default_branch_ignored():
    assert parse_push_event(_push(ref="refs/heads/feature-x")) is None


def test_branch_deletion_ignored():
    assert parse_push_event(_push(after="0000000000000000000000000000000000000000")) is None


def test_net_effect_remove_then_readd_is_changed():
    payload = _push(commits=[
        {"added": [], "modified": [], "removed": ["x.py"]},
        {"added": ["x.py"], "modified": [], "removed": []},
    ])
    req = parse_push_event(payload)
    assert req is not None
    assert "x.py" in req.changed
    assert "x.py" not in req.removed


def test_empty_push_ignored():
    assert parse_push_event(_push(commits=[])) is None


def test_non_push_event_ignored():
    assert parse_push_event({"object_kind": "note"}) is None
