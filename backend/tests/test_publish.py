"""GitLabPublisher posts as the triggering user via sudo, with a fallback."""

from types import SimpleNamespace

import pytest
from app.gitlab.publish import GitLabPublisher
from gitlab.exceptions import GitlabCreateError


class FakeManager:
    """Records create() calls; raises for sudo calls when ``refuse_sudo`` is set."""

    def __init__(self, refuse_sudo: int | None = None):
        self.refuse_sudo = refuse_sudo
        self.calls: list[tuple[dict, dict]] = []

    def create(self, data, **kwargs):
        self.calls.append((data, kwargs))
        if "sudo" in kwargs and self.refuse_sudo:
            raise GitlabCreateError("refused", response_code=self.refuse_sudo)
        return SimpleNamespace(web_url="https://gitlab/note/1")


def _publisher(manager, post_as="alice"):
    mr = SimpleNamespace(notes=manager, discussions=manager)
    return GitLabPublisher(mr, refs=None, file_diffs=[], post_as=post_as)


def test_posts_as_triggering_user():
    m = FakeManager()
    _publisher(m).post_summary("hello")
    assert m.calls == [({"body": "hello"}, {"sudo": "alice"})]


@pytest.mark.parametrize("code", [401, 403, 404])
def test_falls_back_and_credits_requester_when_sudo_refused(code):
    m = FakeManager(refuse_sudo=code)
    pub = _publisher(m)
    pub.post_summary("hello")
    data, kwargs = m.calls[-1]
    assert kwargs == {}
    assert data["body"] == "hello\n\n_Requested by @alice_"
    # Sudo is dropped for the rest of the run: no second refused attempt.
    pub.post_summary("again")
    assert m.calls[-1] == ({"body": "again\n\n_Requested by @alice_"}, {})
    assert len(m.calls) == 3


def test_non_sudo_error_is_not_swallowed():
    m = FakeManager(refuse_sudo=400)
    with pytest.raises(GitlabCreateError):
        _publisher(m).post_summary("hello")


def test_no_requester_posts_plainly():
    m = FakeManager()
    _publisher(m, post_as=None).post_summary("hello")
    assert m.calls == [({"body": "hello"}, {})]
