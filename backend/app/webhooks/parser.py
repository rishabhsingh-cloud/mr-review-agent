"""Parse GitLab 'Note' webhooks into review requests.

The agent triggers only on an MR comment whose first token is the configured
command (default ``/review``). An optional second token narrows the scope to a
single specialist agent, e.g. ``/review security``.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

# Scopes that map to a single specialist agent; anything else means "all".
VALID_SCOPES = {"security", "quality", "tests", "docs"}
SCOPE_ALL = "all"


@dataclass(frozen=True)
class ReviewRequest:
    project_id: int
    mr_iid: int
    note_id: int
    author_username: str
    command_scope: str  # "all" | "security" | "quality" | "tests" | "docs"
    head_sha: str | None = None


def verify_webhook_token(provided: str | None, expected: str) -> bool:
    """Constant-time comparison of the X-Gitlab-Token header against the secret."""
    if not expected:
        # Refuse to run wide open if no secret is configured.
        return False
    return hmac.compare_digest(provided or "", expected)


def parse_review_command(note: str, review_command: str) -> str | None:
    """Return the requested scope if ``note`` invokes the command, else None.

    The command must be the FIRST whitespace-delimited token, so a stray mention
    like "see /review docs later" or a word like "/reviewer" does not trigger.
    """
    tokens = (note or "").strip().split()
    if not tokens or tokens[0] != review_command:
        return None
    if len(tokens) >= 2 and tokens[1].lower() in VALID_SCOPES:
        return tokens[1].lower()
    return SCOPE_ALL


def parse_note_event(payload: dict, review_command: str) -> ReviewRequest | None:
    """Extract a ReviewRequest from a GitLab Note webhook payload.

    Returns None for anything that is not an MR comment invoking the command.
    """
    if payload.get("object_kind") != "note":
        return None
    obj = payload.get("object_attributes") or {}
    if obj.get("noteable_type") != "MergeRequest":
        return None

    scope = parse_review_command(obj.get("note", ""), review_command)
    if scope is None:
        return None

    mr = payload.get("merge_request") or {}
    user = payload.get("user") or {}
    project = payload.get("project") or {}

    project_id = project.get("id") or mr.get("target_project_id")
    mr_iid = mr.get("iid")
    note_id = obj.get("id")
    if project_id is None or mr_iid is None or note_id is None:
        return None

    return ReviewRequest(
        project_id=int(project_id),
        mr_iid=int(mr_iid),
        note_id=int(note_id),
        author_username=user.get("username", ""),
        command_scope=scope,
        head_sha=(mr.get("last_commit") or {}).get("id"),
    )
