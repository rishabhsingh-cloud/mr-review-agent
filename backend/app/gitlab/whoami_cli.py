"""CLI: verify GitLab connectivity + token. Optionally check project access.

Also reports whether the token can post comments as the /review user
(admin account + ``sudo`` scope).

Usage (from backend/, venv active, GITLAB_URL/GITLAB_TOKEN set in .env):
    python -m app.gitlab.whoami_cli
    python -m app.gitlab.whoami_cli --project 123
"""

from __future__ import annotations

import argparse

from app.config import get_settings
from app.gitlab.client import get_gitlab_client, get_project


def main() -> None:
    parser = argparse.ArgumentParser(description="Check GitLab auth + access")
    parser.add_argument("--project", type=int, default=None, help="project id to test access")
    args = parser.parse_args()

    settings = get_settings()
    print(f"GitLab URL: {settings.gitlab_url}")
    print(f"SSL verify: {settings.gitlab_ssl_verify}")

    gl = get_gitlab_client()
    try:
        gl.auth()
    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ Auth failed: {exc}")
        print("Check GITLAB_URL, GITLAB_TOKEN (scope: api), and network reachability.")
        raise SystemExit(1)

    user = gl.user
    print(f"\n✅ Authenticated as: {user.username} ({getattr(user, 'name', '')})")

    # Posting comments as the /review user needs an admin token with `sudo`.
    is_admin = bool(getattr(user, "is_admin", False))
    try:
        scopes = gl.http_get("/personal_access_tokens/self").get("scopes", [])
    except Exception:  # noqa: BLE001 — endpoint missing on older GitLab
        scopes = None
    print(f"{'✅' if is_admin else '❌'} Admin user: {is_admin}")
    if scopes is not None:
        print(f"   Token scopes: {', '.join(scopes) or '(none)'}")
        has_sudo = "sudo" in scopes
    else:
        # Older GitLab can't list scopes: probe with a harmless sudo read instead.
        try:
            gl.http_get("/user", sudo=user.username)
            has_sudo = True
        except Exception as exc:  # noqa: BLE001
            has_sudo = False
            print(f"   sudo test call refused: {exc}")
    print(f"{'✅' if has_sudo else '❌'} sudo scope: {has_sudo}")
    if is_admin and has_sudo:
        print("✅ Comments will be posted as the user who typed /review.")
    else:
        print("❌ Comments will be posted as this token's owner (with a 'Requested by' line).")

    if args.project is not None:
        try:
            project = get_project(args.project)
            print(f"✅ Project access: {project.path_with_namespace} (id={project.id})")
        except Exception as exc:  # noqa: BLE001
            print(f"❌ Cannot access project {args.project}: {exc}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
