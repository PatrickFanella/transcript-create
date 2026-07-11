"""Behavioral coverage for the pseudonymous analytics identity boundary."""

from __future__ import annotations

import base64

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.settings import Settings


def _analytics_app(*, environment: str = "development") -> FastAPI:
    from app.analytics_identity import AnalyticsIdentityMiddleware

    config = Settings(
        _env_file=None,
        ENVIRONMENT=environment,
        ANALYTICS_HMAC_SECRET="analytics-only-secret-with-32-bytes",
    )
    app = FastAPI()
    app.add_middleware(AnalyticsIdentityMiddleware, config=config)

    @app.get("/subject")
    def subject(request: Request):
        return {"subject": request.state.analytics_subject_id}

    return app


def test_first_request_receives_private_analytics_cookie_and_subject() -> None:
    with TestClient(_analytics_app()) as client:
        response = client.get("/subject")

    assert response.status_code == 200
    assert len(response.json()["subject"]) == 64
    cookie = response.cookies["ha_analytics"]
    assert len(base64.urlsafe_b64decode(cookie + "=")) == 32

    set_cookie = response.headers["set-cookie"]
    assert "ha_analytics=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/" in set_cookie
    assert "Max-Age=31536000" in set_cookie
    assert "Secure" not in set_cookie
    assert response.headers["cache-control"] == "private, no-store"


def test_analytics_identity_is_stable_without_reissuing_cookie() -> None:
    with TestClient(_analytics_app()) as client:
        first = client.get("/subject")
        second = client.get("/subject")

    assert second.json()["subject"] == first.json()["subject"]
    assert "set-cookie" not in second.headers


def test_malformed_analytics_cookie_is_rotated() -> None:
    with TestClient(_analytics_app()) as client:
        response = client.get("/subject", cookies={"ha_analytics": "not-valid"})

    assert response.cookies["ha_analytics"] != "not-valid"
    assert len(base64.urlsafe_b64decode(response.cookies["ha_analytics"] + "=")) == 32


def test_distinct_analytics_cookies_produce_distinct_subjects() -> None:
    app = _analytics_app()
    with TestClient(app) as first_client, TestClient(app) as second_client:
        first = first_client.get("/subject")
        second = second_client.get("/subject")

    assert first.json()["subject"] != second.json()["subject"]


def test_production_analytics_cookie_is_secure() -> None:
    with TestClient(_analytics_app(environment="production")) as client:
        response = client.get("/subject")

    assert "Secure" in response.headers["set-cookie"]
