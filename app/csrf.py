"""Session-bound CSRF protection for cookie-authenticated mutations."""

import hashlib
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .common.session import SESSION_COOKIE, get_session_token
from .middleware import apply_security_headers
from .settings import settings

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def csrf_token(raw_session_token: str) -> str:
    return hmac.new(
        settings.SESSION_SECRET.encode("utf-8"), raw_session_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def allowed_origins() -> set[str]:
    return {
        settings.FRONTEND_ORIGIN,
        *(origin.strip() for origin in settings.CORS_ALLOW_ORIGINS.split(",") if origin.strip()),
    }


def csrf_failure() -> Response:
    """Return the standard protected response even when middleware short-circuits."""
    return apply_security_headers(
        JSONResponse(
            status_code=403,
            content={"error": "csrf_failed", "message": "CSRF validation failed"},
        )
    )


async def csrf_middleware(request: Request, call_next):
    """Protect unsafe requests when a raw session cookie accompanies them.

    API-key-only requests deliberately have no cookie and therefore remain
    usable by non-browser clients. A mixed credential request is a browser
    session request and must pass CSRF validation.
    """
    # Logout clears a browser cookie even when SameSite prevents that cookie
    # from accompanying a cross-site POST.  It therefore needs an origin check
    # independently of the usual cookie-gated CSRF protection below.
    is_logout = request.method == "POST" and request.url.path == "/auth/logout"
    if is_logout and request.headers.get("origin") not in allowed_origins():
        return csrf_failure()

    token = get_session_token(request)
    # Presence, rather than truthiness, matters: ``tc_session=`` is still a
    # browser credential and cannot turn an invalid API-key request into an
    # API-key-only exemption.
    has_session_cookie = SESSION_COOKIE in request.cookies
    if request.method not in UNSAFE_METHODS or not has_session_cookie:
        return await call_next(request)

    # An empty or malformed session cookie must fail closed rather than be
    # treated as an API-key-only request.
    if not token:
        return csrf_failure()

    origin = request.headers.get("origin")
    if origin and origin not in allowed_origins():
        return csrf_failure()
    if not origin and (is_logout or settings.ENVIRONMENT.strip().lower() == "production"):
        return csrf_failure()
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return csrf_failure()
    supplied = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(supplied, csrf_token(token)):
        return csrf_failure()
    return await call_next(request)
