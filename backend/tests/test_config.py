"""Tests for the reviewer allowlist logic (incl. wildcard)."""

from __future__ import annotations

from app.config import Settings


def test_allowlist_specific_users():
    s = Settings(reviewer_allowlist=["alice", "bob"])
    assert s.is_reviewer_allowed("alice") is True
    assert s.is_reviewer_allowed("carol") is False
    assert s.is_reviewer_allowed(None) is False


def test_allowlist_wildcard_allows_everyone():
    s = Settings(reviewer_allowlist=["*"])
    assert s.is_reviewer_allowed("anyone") is True
    assert s.is_reviewer_allowed("someone-else") is True
    # still rejects empty/None username
    assert s.is_reviewer_allowed("") is False
    assert s.is_reviewer_allowed(None) is False


def test_allowlist_empty_blocks_all():
    s = Settings(reviewer_allowlist=[])
    assert s.is_reviewer_allowed("alice") is False


def test_allowlist_comma_string_is_split():
    s = Settings(reviewer_allowlist="alice, bob ,carol")
    assert s.is_reviewer_allowed("bob") is True
    assert s.is_reviewer_allowed("dave") is False
