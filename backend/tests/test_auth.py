"""Tests for Basic-auth credential checking and path exemptions."""

from __future__ import annotations

import base64

from app.auth import _credentials_ok, is_exempt


def _hdr(user: str, pw: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def test_valid_credentials():
    assert _credentials_ok(_hdr("admin", "s3cret"), "admin", "s3cret") is True


def test_wrong_password():
    assert _credentials_ok(_hdr("admin", "nope"), "admin", "s3cret") is False


def test_wrong_user():
    assert _credentials_ok(_hdr("root", "s3cret"), "admin", "s3cret") is False


def test_malformed_headers():
    assert _credentials_ok("", "admin", "s3cret") is False
    assert _credentials_ok("Bearer xyz", "admin", "s3cret") is False
    assert _credentials_ok("Basic !!notbase64!!", "admin", "s3cret") is False


def test_exempt_paths():
    assert is_exempt("/health") is True
    assert is_exempt("/webhooks/gitlab") is True
    assert is_exempt("/reviews") is False
    assert is_exempt("/hitl/1/approve") is False
    assert is_exempt("/dashboard/") is False
