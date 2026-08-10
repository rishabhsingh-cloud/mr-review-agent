"""Tests for webhook token verification and /review command parsing."""

from __future__ import annotations

from app.webhooks.parser import (
    parse_note_event,
    parse_review_command,
    verify_webhook_token,
)


# ── token verification ──


def test_token_matches():
    assert verify_webhook_token("secret", "secret") is True


def test_token_mismatch():
    assert verify_webhook_token("nope", "secret") is False


def test_token_empty_secret_refused():
    # No configured secret must never accept requests.
    assert verify_webhook_token("anything", "") is False
    assert verify_webhook_token(None, "secret") is False


# ── command parsing ──


def test_bare_command_is_all():
    assert parse_review_command("/review", "/review") == "all"


def test_scoped_command():
    assert parse_review_command("/review security", "/review") == "security"
    assert parse_review_command("/review DOCS", "/review") == "docs"


def test_unknown_scope_falls_back_to_all():
    assert parse_review_command("/review everything", "/review") == "all"


def test_non_command_ignored():
    assert parse_review_command("looks good to me", "/review") is None
    assert parse_review_command("/reviewer please", "/review") is None
    assert parse_review_command("see /review later", "/review") is None


# ── full note event ──


def _note_payload(note: str = "/review security", username: str = "alice") -> dict:
    return {
        "object_kind": "note",
        "user": {"username": username},
        "project": {"id": 42},
        "object_attributes": {
            "id": 1001,
            "noteable_type": "MergeRequest",
            "note": note,
        },
        "merge_request": {"iid": 7, "last_commit": {"id": "deadbeef"}},
    }


def test_parse_note_event_ok():
    req = parse_note_event(_note_payload(), "/review")
    assert req is not None
    assert req.project_id == 42
    assert req.mr_iid == 7
    assert req.note_id == 1001
    assert req.author_username == "alice"
    assert req.command_scope == "security"
    assert req.head_sha == "deadbeef"


def test_parse_note_event_non_mr_ignored():
    payload = _note_payload()
    payload["object_attributes"]["noteable_type"] = "Issue"
    assert parse_note_event(payload, "/review") is None


def test_parse_note_event_non_command_ignored():
    assert parse_note_event(_note_payload(note="thanks!"), "/review") is None


def test_parse_note_event_wrong_kind_ignored():
    payload = _note_payload()
    payload["object_kind"] = "push"
    assert parse_note_event(payload, "/review") is None
