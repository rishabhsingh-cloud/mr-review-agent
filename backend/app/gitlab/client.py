"""Thin wrapper around python-gitlab, configured for the self-hosted instance."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import gitlab
from gitlab.v4.objects import Project, ProjectMergeRequest

from app.config import get_settings


@lru_cache
def get_gitlab_client() -> gitlab.Gitlab:
    """Return a cached, authenticated Gitlab client for the configured instance.

    ``gitlab_ssl_verify`` accepts a bool or a CA-bundle path (as a string in
    .env). python-gitlab passes it straight through to requests.
    """
    settings = get_settings()
    return gitlab.Gitlab(
        url=settings.gitlab_url,
        private_token=settings.gitlab_token,
        ssl_verify=settings.gitlab_ssl_verify,
        timeout=settings.gitlab_timeout,
    )


def get_project(project_id: int) -> Project:
    return get_gitlab_client().projects.get(project_id)


def get_merge_request(project_id: int, mr_iid: int) -> ProjectMergeRequest:
    return get_project(project_id).mergerequests.get(mr_iid)


def get_mr_changes(mr: ProjectMergeRequest) -> dict[str, Any]:
    """Full change set for an MR, including ``diff_refs`` and per-file diffs."""
    return mr.changes()
