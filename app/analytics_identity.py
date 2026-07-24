"""Pseudonymous, cookie-backed identity for first-party analytics."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .settings import Settings, settings

ANALYTICS_COOKIE_NAME = "ha_analytics"
ANALYTICS_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
_COOKIE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def generate_analytics_cookie() -> str:
    """Return 32 random bytes encoded as unpadded base64url."""

    return secrets.token_urlsafe(32)


def is_valid_analytics_cookie(value: str | None) -> bool:
    """Accept only the canonical encoding emitted by this application."""

    if not value or not _COOKIE_PATTERN.fullmatch(value):
        return False
    try:
        raw = base64.urlsafe_b64decode(value + "=")
    except (ValueError, TypeError):
        return False
    return len(raw) == 32 and base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") == value


def analytics_hmac_secret(config: Settings = settings) -> str:
    """Resolve the analytics key without exposing it through logs or responses."""

    dedicated = config.ANALYTICS_HMAC_SECRET.strip()
    if dedicated:
        return dedicated
    if config.ENVIRONMENT.strip().lower() != "production":
        return config.SESSION_SECRET
    raise RuntimeError("ANALYTICS_HMAC_SECRET is required in production")


def derive_analytics_subject_id(cookie_value: str, config: Settings = settings) -> str:
    """Derive the stable, non-reversible identifier stored with events."""

    return hmac.new(
        analytics_hmac_secret(config).encode("utf-8"),
        cookie_value.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def get_analytics_subject_id(request: Request) -> str:
    """Return the subject established by :class:`AnalyticsIdentityMiddleware`."""

    subject = getattr(request.state, "analytics_subject_id", None)
    if not isinstance(subject, str) or len(subject) != 64:
        raise RuntimeError("Analytics identity middleware is not configured")
    return subject


class AnalyticsIdentityMiddleware(BaseHTTPMiddleware):
    """Attach a pseudonymous analytics subject to every request."""

    def __init__(self, app, config: Settings = settings):
        super().__init__(app)
        self.config = config

    async def dispatch(self, request: Request, call_next):
        cookie_value = request.cookies.get(ANALYTICS_COOKIE_NAME)
        must_set_cookie = not is_valid_analytics_cookie(cookie_value)
        if must_set_cookie:
            cookie_value = generate_analytics_cookie()

        request.state.analytics_subject_id = derive_analytics_subject_id(cookie_value, self.config)
        response = await call_next(request)

        if must_set_cookie:
            response.set_cookie(
                key=ANALYTICS_COOKIE_NAME,
                value=cookie_value,
                max_age=ANALYTICS_COOKIE_MAX_AGE,
                path="/",
                secure=self.config.ENVIRONMENT.strip().lower() == "production",
                httponly=True,
                samesite="lax",
            )
            # A shared cache must never replay one visitor's newly issued
            # analytics cookie to another visitor.
            response.headers["Cache-Control"] = "private, no-store"
        return response
