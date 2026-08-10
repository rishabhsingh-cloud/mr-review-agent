"""Publish review output back to a GitLab MR: inline discussions + summary note.

Inline comments are posted as discussions pinned to a diff position. If GitLab
rejects a position (e.g. the line isn't in the current diff version), we fall
back to including that finding in the summary note so nothing is silently lost.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from gitlab.exceptions import GitlabCreateError
from gitlab.v4.objects import ProjectMergeRequest

from app.gitlab.diff import (
    DiffRefs,
    FileDiff,
    find_file_diff,
    position_for_new_line,
)

logger = logging.getLogger(__name__)


@dataclass
class InlineComment:
    """A comment to attach to a specific file/line in the MR diff."""

    file_path: str
    new_line: int
    body: str


@dataclass
class PublishResult:
    inline_posted: int
    fell_back: int
    summary_note_url: str | None


class GitLabPublisher:
    def __init__(self, mr: ProjectMergeRequest, refs: DiffRefs, file_diffs: list[FileDiff]):
        self.mr = mr
        self.refs = refs
        self.file_diffs = file_diffs

    def _post_discussion(self, body: str, position: dict[str, Any]) -> bool:
        try:
            self.mr.discussions.create({"body": body, "position": position})
            return True
        except GitlabCreateError as exc:
            logger.warning("Inline discussion rejected (%s); will fall back to summary", exc)
            return False

    def post_inline(self, comment: InlineComment) -> bool:
        """Post one inline comment. Returns True if pinned, False if not mappable."""
        file_diff = find_file_diff(self.file_diffs, comment.file_path)
        if file_diff is None:
            return False
        position = position_for_new_line(file_diff, self.refs, comment.new_line)
        if position is None:
            return False
        return self._post_discussion(comment.body, position)

    def post_summary(self, body: str) -> str | None:
        note = self.mr.notes.create({"body": body})
        return getattr(note, "web_url", None)

    def approve(self) -> None:
        self.mr.approve()

    def publish(
        self, inline_comments: list[InlineComment], summary_header: str
    ) -> PublishResult:
        """Post all inline comments, then a summary note that also carries any
        findings that couldn't be pinned to a diff line."""
        posted = 0
        fallbacks: list[InlineComment] = []
        for c in inline_comments:
            if self.post_inline(c):
                posted += 1
            else:
                fallbacks.append(c)

        summary = summary_header
        if fallbacks:
            summary += "\n\n---\n**Additional findings (could not be pinned to a line):**\n"
            for c in fallbacks:
                summary += f"\n- `{c.file_path}:{c.new_line}` — {c.body}"

        url = self.post_summary(summary)
        return PublishResult(
            inline_posted=posted, fell_back=len(fallbacks), summary_note_url=url
        )
