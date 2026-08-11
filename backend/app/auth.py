"""HTTP Basic auth for the dashboard + read/action API.

Enabled only when `dashboard_password` is set. Exempts the GitLab webhook (it
authenticates with its own X-Gitlab-Token) and /health (monitoring).
"""

from __future__ import annotations

import base64
import binascii
import hmac

from starlette.requests import Request
from starlette.responses import Response

# Paths that must NOT require Basic auth.
_EXEMPT_PREFIXES = ("/webhooks/",)
_EXEMPT_EXACT = {"/health"}

_UNAUTHORIZED = Response(
    status_code=401,
    content="Authentication required",
    headers={"WWW-Authenticate": 'Basic realm="MR Review Agent"'},
)


def _credentials_ok(header: str, user: str, password: str) -> bool:
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    got_user, _, got_pass = decoded.partition(":")
    # Constant-time comparison for both fields.
    return hmac.compare_digest(got_user, user) and hmac.compare_digest(got_pass, password)


def is_exempt(path: str) -> bool:
    return path in _EXEMPT_EXACT or path.startswith(_EXEMPT_PREFIXES)


async def basic_auth_middleware(request: Request, call_next):
    from app.config import get_settings

    settings = get_settings()
    if not settings.auth_enabled or is_exempt(request.url.path):
        return await call_next(request)

    header = request.headers.get("Authorization", "")
    if _credentials_ok(header, settings.dashboard_user, settings.dashboard_password):
        return await call_next(request)
    return Response(
        status_code=401,
        content="Authentication required",
        headers={"WWW-Authenticate": 'Basic realm="MR Review Agent"'},
    )
