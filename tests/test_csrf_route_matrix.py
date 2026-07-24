"""Central CSRF behavior across every cookie-authenticated mutation family."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.csrf import csrf_token
from app.security import generate_api_key
from app.settings import settings

UNSAFE_ROUTE_FAMILIES = (
    ("patch", "/account", {"json": {}}),
    ("post", "/admin/archive/periods", {"json": {}}),
    ("post", "/api-keys", {"json": {}}),
    ("post", "/users/me/favorites", {"json": {}}),
    ("post", "/users/me/saved-searches", {"json": {}}),
    ("delete", f"/videos/{uuid.uuid4()}", {}),
    ("post", "/vocabularies", {"json": {}}),
    ("post", "/jobs", {"json": {}}),
    ("post", "/events", {"json": {}}),
    ("post", "/events/batch", {"json": {"events": []}}),
)


@pytest.mark.parametrize(("method", "path", "kwargs"), UNSAFE_ROUTE_FAMILIES)
def test_cookie_unsafe_routes_require_valid_csrf_before_downstream_auth(client, method, path, kwargs):
    request = getattr(client, method)
    cookies = {"tc_session": "not-a-real-session"}

    assert request(path, cookies=cookies, **kwargs).json()["error"] == "csrf_failed"
    assert request(path, cookies=cookies, headers={"Origin": settings.FRONTEND_ORIGIN, "X-CSRF-Token": "bad"}, **kwargs).json()["error"] == "csrf_failed"

    response = request(
        path,
        cookies=cookies,
        headers={"Origin": settings.FRONTEND_ORIGIN, "X-CSRF-Token": csrf_token("not-a-real-session")},
        **kwargs,
    )
    assert response.json().get("error") != "csrf_failed"


def test_api_key_exemption_requires_a_valid_key_and_no_session_cookie(client, db_session):
    user_id = uuid.uuid4()
    key, key_hash = generate_api_key()
    db_session.execute(
        text("INSERT INTO users (id, email, oauth_provider, oauth_subject) VALUES (:id, 'csrf-key@example.com', 'google', 'csrf-key')"),
        {"id": user_id},
    )
    db_session.execute(
        text("INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix) VALUES (:id, :user_id, 'CSRF', :hash, :prefix)"),
        {"id": uuid.uuid4(), "user_id": user_id, "hash": key_hash, "prefix": key[:10] + "..."},
    )
    db_session.commit()

    # A valid key without a browser session reaches normal downstream validation.
    valid = client.post("/jobs", headers={"X-API-Key": key}, json={})
    assert valid.status_code == 422

    invalid_without_cookie = client.post("/jobs", headers={"X-API-Key": "tc_not_valid"}, json={})
    assert invalid_without_cookie.status_code == 401

    # Merely auth-looking headers and an explicit empty session cookie are not exemptions.
    invalid = client.post("/jobs", headers={"X-API-Key": "tc_not_valid"}, cookies={"tc_session": ""}, json={})
    assert invalid.json()["error"] == "csrf_failed"

    # Mixed credentials remain browser-session requests, even with a valid key.
    mixed = client.post("/jobs", headers={"X-API-Key": key}, cookies={"tc_session": "session"}, json={})
    assert mixed.json()["error"] == "csrf_failed"
    authorization_mixed = client.post("/jobs", headers={"Authorization": "Bearer auth-looking"}, cookies={"tc_session": "session"}, json={})
    assert authorization_mixed.json()["error"] == "csrf_failed"
    protected = client.post(
        "/jobs",
        headers={"X-API-Key": key, "Origin": settings.FRONTEND_ORIGIN, "X-CSRF-Token": csrf_token("session")},
        cookies={"tc_session": "session"},
        json={},
    )
    assert protected.status_code == 422


def test_csrf_failures_include_security_headers(client):
    response = client.post("/jobs", cookies={"tc_session": "session"}, json={})
    assert response.status_code == 403
    assert response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"


def test_cookie_requests_reject_invalid_origin(client):
    response = client.post(
        "/jobs",
        cookies={"tc_session": "session"},
        headers={"Origin": "https://attacker.example", "X-CSRF-Token": csrf_token("session")},
        json={},
    )
    assert response.json()["error"] == "csrf_failed"


def test_production_cookie_requests_require_origin(client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    response = client.post(
        "/jobs",
        cookies={"tc_session": "session"},
        headers={"X-CSRF-Token": csrf_token("session")},
        json={},
    )
    assert response.json()["error"] == "csrf_failed"
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_cookie_requests_reject_cross_site_fetch_metadata(client):
    response = client.post(
        "/jobs",
        cookies={"tc_session": "session"},
        headers={
            "Origin": settings.FRONTEND_ORIGIN,
            "Sec-Fetch-Site": "cross-site",
            "X-CSRF-Token": csrf_token("session"),
        },
        json={},
    )
    assert response.json()["error"] == "csrf_failed"


def test_cookie_authenticated_logout_requires_and_accepts_csrf(client, db_session):
    user_id = uuid.uuid4()
    token = secrets.token_urlsafe(32)
    db_session.execute(
        text("INSERT INTO users (id, email, oauth_provider, oauth_subject) VALUES (:id, 'csrf-logout@example.com', 'google', 'csrf-logout')"),
        {"id": user_id},
    )
    db_session.execute(
        text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:user_id, :token_hash, :expires_at)"),
        {
            "user_id": user_id,
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "expires_at": datetime.utcnow() + timedelta(days=1),
        },
    )
    db_session.commit()

    missing = client.post("/auth/logout", cookies={"tc_session": token})
    assert missing.status_code == 403
    assert missing.json()["error"] == "csrf_failed"

    valid = client.post(
        "/auth/logout",
        cookies={"tc_session": token},
        headers={"Origin": settings.FRONTEND_ORIGIN, "X-CSRF-Token": csrf_token(token)},
    )
    assert valid.status_code == 200
    assert db_session.execute(text("SELECT COUNT(*) FROM sessions WHERE user_id=:user_id"), {"user_id": user_id}).scalar() == 0
