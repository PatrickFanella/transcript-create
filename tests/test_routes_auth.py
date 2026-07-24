"""Tests for auth routes."""

import secrets
import uuid
from datetime import datetime, timedelta
from hashlib import sha256
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.csrf import csrf_token
from app.settings import settings


class TestAuthRoutes:
    """Tests for /auth endpoints."""

    def test_auth_me_unauthenticated(self, client: TestClient):
        """Test /auth/me endpoint without authentication."""
        response = client.get("/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["user"] is None
        assert data["role"] is None
        assert data["capabilities"] == []

    def test_auth_me_authenticated(self, client: TestClient, db_session):
        """Test /auth/me endpoint with authenticated user."""
        # Create a test user and session
        user_id = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)

        db_session.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject, plan) "
                "VALUES (:id, :email, :name, 'google', 'test123', 'free')"
            ),
            {"id": str(user_id), "email": "test@example.com", "name": "Test User"},
        )
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {
                "uid": str(user_id),
                "token_hash": sha256(session_token.encode()).hexdigest(),
                "exp": datetime.utcnow() + timedelta(days=1),
            },
        )
        db_session.commit()

        # Make request with session cookie
        response = client.get("/auth/me", cookies={"tc_session": session_token})
        assert response.status_code == 200
        data = response.json()
        assert data["user"] is not None
        assert data["user"]["email"] == "test@example.com"
        assert data["user"]["name"] == "Test User"
        assert data["user"]["plan"] == "free"
        assert data["role"] == "user"
        assert isinstance(data["capabilities"], list)

    def test_auth_me_moderator_response_contract(self, client: TestClient, db_session):
        """A moderator session exposes its effective role and capabilities."""
        user_id = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)

        db_session.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject, role) "
                "VALUES (:id, :email, :name, 'google', 'moderator-auth-me', 'moderator')"
            ),
            {"id": str(user_id), "email": "moderator@example.com", "name": "Moderator"},
        )
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {
                "uid": str(user_id),
                "token_hash": sha256(session_token.encode()).hexdigest(),
                "exp": datetime.utcnow() + timedelta(days=1),
            },
        )
        db_session.commit()

        response = client.get("/auth/me", cookies={"tc_session": session_token})

        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "moderator"
        assert "moderation:access" in data["capabilities"]
        assert "admin:access" not in data["capabilities"]

    def test_auth_me_expired_session(self, client: TestClient, db_session):
        """Test /auth/me with expired session."""
        user_id = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)

        db_session.execute(
            text(
                "INSERT INTO users (id, email, oauth_provider, oauth_subject) " "VALUES (:id, :email, 'google', 'test')"
            ),
            {"id": str(user_id), "email": "test@example.com"},
        )
        # Create expired session
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {
                "uid": str(user_id),
                "token_hash": sha256(session_token.encode()).hexdigest(),
                "exp": datetime.utcnow() - timedelta(days=1),
            },
        )
        db_session.commit()

        response = client.get("/auth/me", cookies={"tc_session": session_token})
        assert response.status_code == 200
        data = response.json()
        # Should return no user for expired session
        assert data["user"] is None

    def test_auth_logout(self, client: TestClient, db_session):
        """Test logout endpoint."""
        user_id = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)

        db_session.execute(
            text(
                "INSERT INTO users (id, email, oauth_provider, oauth_subject) " "VALUES (:id, :email, 'google', 'test')"
            ),
            {"id": str(user_id), "email": "test@example.com"},
        )
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {
                "uid": str(user_id),
                "token_hash": sha256(session_token.encode()).hexdigest(),
                "exp": datetime.utcnow() + timedelta(days=1),
            },
        )
        db_session.commit()

        # Logout
        response = client.post(
            "/auth/logout",
            cookies={"tc_session": session_token},
            headers={"origin": settings.FRONTEND_ORIGIN, "x-csrf-token": csrf_token(session_token)},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

        # Verify session is deleted
        result = db_session.execute(
            text("SELECT * FROM sessions WHERE token_hash = :token_hash"),
            {"token_hash": sha256(session_token.encode()).hexdigest()},
        ).first()
        assert result is None

    def test_auth_logout_no_session(self, client: TestClient):
        """Test logout without a session."""
        response = client.post("/auth/logout", headers={"origin": settings.FRONTEND_ORIGIN})
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_auth_logout_without_cookie_rejects_missing_or_cross_site_origin(self, client: TestClient):
        for headers in ({}, {"origin": "https://attacker.example"}):
            response = client.post("/auth/logout", headers=headers)
            assert response.status_code == 403
            assert "set-cookie" not in response.headers

        response = client.post("/auth/logout", headers={"origin": settings.FRONTEND_ORIGIN})
        assert response.status_code == 200
        assert "tc_session=" in response.headers["set-cookie"]

    def test_auth_logout_rolls_back_session_delete_when_audit_write_fails(
        self, client: TestClient, test_engine, monkeypatch
    ):
        user_id, session_token = uuid.uuid4(), "logout-audit-failure-token"
        from app.db import SessionLocal, get_db
        from app.main import app

        # Seed and verify outside the fixture transaction so route rollback
        # cannot erase the fixture's uncommitted state instead of its delete.
        with test_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, oauth_provider, oauth_subject) VALUES (:id, :email, 'google', 'logout-audit')"
                ),
                {"id": str(user_id), "email": "logout-audit@example.com"},
            )
            connection.execute(
                text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:id, :token_hash, :expires)"),
                {
                    "id": str(user_id),
                    "token_hash": sha256(session_token.encode()).hexdigest(),
                    "expires": datetime.utcnow() + timedelta(hours=1),
                },
            )

        original_override = app.dependency_overrides[get_db]

        def committed_db():
            session = SessionLocal(bind=test_engine)
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = committed_db

        def fail_audit(*args, **kwargs):
            raise RuntimeError("audit storage unavailable")

        try:
            monkeypatch.setattr("app.routes.auth.write_audit_event", fail_audit)
            response = client.post(
                "/auth/logout",
                cookies={"tc_session": session_token},
                headers={"origin": settings.FRONTEND_ORIGIN, "x-csrf-token": csrf_token(session_token)},
            )

            assert response.status_code == 500
            assert response.json() == {"error": "database_error", "message": "Logout could not be completed"}
            with test_engine.connect() as verification_connection:
                assert (
                    verification_connection.execute(
                        text("SELECT 1 FROM sessions WHERE token_hash=:token_hash"),
                        {"token_hash": sha256(session_token.encode()).hexdigest()},
                    ).scalar()
                    == 1
                )
                assert (
                    verification_connection.execute(
                        text("SELECT count(*) FROM audit_logs WHERE user_id=:user_id AND action='logout'"),
                        {"user_id": str(user_id)},
                    ).scalar()
                    == 0
                )
        finally:
            app.dependency_overrides[get_db] = original_override
            with test_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM sessions WHERE token_hash=:token_hash"),
                    {"token_hash": sha256(session_token.encode()).hexdigest()},
                )
                connection.execute(text("DELETE FROM users WHERE id=:user_id"), {"user_id": str(user_id)})

    @patch("app.routes.auth.OAuth", None)
    def test_auth_login_google_no_oauth(self, client: TestClient):
        """Test Google login when OAuth is not available."""
        response = client.get("/auth/login/google")
        assert response.status_code == 503
        data = response.json()
        assert data["error"] == "external_service_error"
        assert "Authentication library not installed" in data["message"]

    @patch("app.routes.auth.OAuth", None)
    def test_auth_login_twitch_no_oauth(self, client: TestClient):
        """Test Twitch login when OAuth is not available."""
        response = client.get("/auth/login/twitch")
        assert response.status_code == 503
        data = response.json()
        assert data["error"] == "external_service_error"
        assert "Authentication library not installed" in data["message"]

    @patch("app.routes.auth.OAuth")
    def test_auth_login_google_redirect(self, mock_oauth_class, client: TestClient):
        """Test Google login redirect (mocked)."""
        mock_oauth = MagicMock()
        mock_oauth_class.return_value = mock_oauth
        mock_google = MagicMock()
        mock_oauth.google = mock_google

        # Mock the authorize_redirect to return a redirect response
        from fastapi.responses import RedirectResponse

        mock_google.authorize_redirect = AsyncMock(
            return_value=RedirectResponse(url="https://accounts.google.com/o/oauth2/auth")
        )

        with (
            patch("app.routes.auth.settings.OAUTH_GOOGLE_CLIENT_ID", "test-client"),
            patch("app.routes.auth.settings.OAUTH_GOOGLE_CLIENT_SECRET", "test-secret"),
        ):
            response = client.get("/auth/login/google", follow_redirects=False)
        # Should redirect to Google OAuth
        assert response.status_code in [307, 302, 200]  # Redirect or success

    @patch("app.routes.auth.OAuth")
    def test_auth_login_twitch_redirect(self, mock_oauth_class, client: TestClient):
        """Test Twitch login redirect (mocked)."""
        mock_oauth = MagicMock()
        mock_twitch = MagicMock()
        mock_oauth.twitch = mock_twitch
        mock_oauth_class.return_value = mock_oauth

        from fastapi.responses import RedirectResponse

        mock_twitch.authorize_redirect = AsyncMock(
            return_value=RedirectResponse(url="https://id.twitch.tv/oauth2/authorize")
        )

        with (
            patch("app.routes.auth.settings.OAUTH_TWITCH_CLIENT_ID", "test-client"),
            patch("app.routes.auth.settings.OAUTH_TWITCH_CLIENT_SECRET", "test-secret"),
            patch("app.routes.auth.settings.OAUTH_TWITCH_REDIRECT_URI", "http://localhost:8000/auth/callback/twitch"),
        ):
            response = client.get("/auth/login/twitch", follow_redirects=False)
        assert response.status_code == 307
        mock_twitch.authorize_redirect.assert_awaited_once()
        args, kwargs = mock_twitch.authorize_redirect.await_args
        assert args[1] == "http://localhost:8000/auth/callback/twitch"
        assert isinstance(kwargs["state"], str) and kwargs["state"]
        assert isinstance(kwargs["nonce"], str) and kwargs["nonce"]

    def test_auth_me_free_plan_has_unrestricted_search(self, client: TestClient, db_session):
        """Free plan users do not receive a retired billing-era search quota."""
        user_id = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)

        db_session.execute(
            text(
                "INSERT INTO users (id, email, oauth_provider, oauth_subject, plan) "
                "VALUES (:id, :email, 'google', 'test', 'free')"
            ),
            {"id": str(user_id), "email": "free@example.com"},
        )
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {
                "uid": str(user_id),
                "token_hash": sha256(session_token.encode()).hexdigest(),
                "exp": datetime.utcnow() + timedelta(days=1),
            },
        )
        db_session.commit()

        response = client.get("/auth/me", cookies={"tc_session": session_token})
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["plan"] == "free"
        assert "search_limit" not in data["user"]

    def test_auth_callback_missing_state(self, client: TestClient):
        """Test OAuth callback without state parameter."""
        # OAuth callbacks typically require state for CSRF protection
        # This test just ensures the endpoint exists and handles missing params
        response = client.get("/auth/callback/google")
        # Will likely fail due to missing OAuth token or other OAuth errors
        # Should return 503 (ExternalServiceError) or 422 (ValidationError)
        assert response.status_code in [422, 503]

    def test_multiple_sessions_same_user(self, client: TestClient, db_session):
        """Test that a user can have multiple active sessions."""
        user_id = uuid.uuid4()
        session_token1 = secrets.token_urlsafe(32)
        session_token2 = secrets.token_urlsafe(32)

        db_session.execute(
            text(
                "INSERT INTO users (id, email, oauth_provider, oauth_subject) " "VALUES (:id, :email, 'google', 'test')"
            ),
            {"id": str(user_id), "email": "multi@example.com"},
        )
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {
                "uid": str(user_id),
                "token_hash": sha256(session_token1.encode()).hexdigest(),
                "exp": datetime.utcnow() + timedelta(days=1),
            },
        )
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {
                "uid": str(user_id),
                "token_hash": sha256(session_token2.encode()).hexdigest(),
                "exp": datetime.utcnow() + timedelta(days=1),
            },
        )
        db_session.commit()

        # Both sessions should work
        response1 = client.get("/auth/me", cookies={"tc_session": session_token1})
        assert response1.status_code == 200
        assert response1.json()["user"]["email"] == "multi@example.com"

        response2 = client.get("/auth/me", cookies={"tc_session": session_token2})
        assert response2.status_code == 200
        assert response2.json()["user"]["email"] == "multi@example.com"
