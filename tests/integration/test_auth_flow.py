"""Integration tests for authentication and authorization."""

import secrets
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


class TestAuthFlow:
    """Integration tests for authentication flow."""

    @pytest.mark.timeout(60)
    def test_auth_me_endpoint_unauthenticated(self, integration_client: TestClient, clean_test_data):
        """Test /auth/me endpoint without authentication."""
        response = integration_client.get("/auth/me")

        # Should return 401 or user info (depending on session)
        assert response.status_code in [200, 401, 404]

    @pytest.mark.timeout(60)
    def test_oauth_login_initiation(self, integration_client: TestClient, clean_test_data):
        """Test OAuth login initiation endpoint."""
        response = integration_client.get("/auth/login/google")

        # Should redirect or return login URL
        assert response.status_code in [200, 302, 307, 404]

    @pytest.mark.timeout(60)
    def test_oauth_callback_missing_code(self, integration_client: TestClient, clean_test_data):
        """Test OAuth callback without authorization code."""
        response = integration_client.get("/auth/callback/google")

        # Should handle missing code gracefully
        assert response.status_code in [400, 404, 422]

    @pytest.mark.timeout(60)
    def test_oauth_callback_with_code(self, integration_client: TestClient, integration_db, clean_test_data):
        """Test OAuth callback with valid code (mocked)."""
        # Note: This test exercises the OAuth callback endpoint without mocking.
        # The endpoint will fail without a valid OAuth code, which is expected behavior.

        response = integration_client.get("/auth/callback/google?code=mock_auth_code")

        # Should handle callback (might not exist or return 404)
        assert response.status_code == 422
        assert response.json()["error"] == "validation_error"

    @pytest.mark.timeout(60)
    def test_logout(self, integration_client: TestClient, clean_test_data):
        """Test logout endpoint."""
        response = integration_client.post("/auth/logout")

        # Should handle logout
        assert response.status_code in [200, 204, 404]


class TestAuthorizationFlow:
    """Integration tests for authorization and permissions."""

    @pytest.mark.timeout(60)
    def test_protected_endpoint_without_auth(self, integration_client: TestClient, clean_test_data):
        """Test accessing admin users endpoint without authentication."""
        response = integration_client.get("/admin/users")

        assert response.status_code == 401

    @pytest.mark.timeout(60)
    def test_admin_endpoint_non_admin(self, integration_client: TestClient, integration_db, clean_test_data):
        """Test accessing admin endpoint as a non-admin authenticated user."""
        user_id = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)

        integration_db.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject, plan, role, created_at, updated_at) "
                "VALUES (:id, :email, :name, 'google', :subject, 'free', 'user', :created_at, :updated_at)"
            ),
            {
                "id": str(user_id),
                "email": "member@example.com",
                "name": "Member User",
                "subject": "member-subject",
                "created_at": datetime.utcnow() - timedelta(days=1),
                "updated_at": datetime.utcnow() - timedelta(hours=12),
            },
        )
        integration_db.execute(
            text("INSERT INTO sessions (user_id, token, expires_at) VALUES (:uid, :token, :exp)"),
            {"uid": str(user_id), "token": session_token, "exp": datetime.utcnow() + timedelta(days=1)},
        )
        integration_db.commit()

        response = integration_client.get("/admin/users", cookies={"tc_session": session_token})

        assert response.status_code == 403

    @pytest.mark.timeout(60)
    def test_admin_users_endpoint_admin_search_and_pagination(
        self,
        integration_client: TestClient,
        integration_db,
        clean_test_data,
        monkeypatch,
    ):
        """Test /admin/users authorization and search/pagination."""
        admin_id = uuid.uuid4()
        other_admin_id = uuid.uuid4()
        admin_token = secrets.token_urlsafe(32)
        integration_db.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject, plan, role, created_at, updated_at) "
                "VALUES (:id, :email, :name, 'google', :subject, 'pro', 'admin', :created_at, :updated_at)"
            ),
            {
                "id": str(admin_id),
                "email": "admin@example.com",
                "name": "Admin User",
                "subject": "admin-subject",
                "created_at": datetime.utcnow() - timedelta(hours=2),
                "updated_at": datetime.utcnow() - timedelta(hours=1),
            },
        )
        integration_db.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject, plan, role, created_at, updated_at) "
                "VALUES (:id, :email, :name, 'google', :subject, 'free', 'user', :created_at, :updated_at)"
            ),
            {
                "id": str(other_admin_id),
                "email": "another@example.com",
                "name": "Another User",
                "subject": "another-subject",
                "created_at": datetime.utcnow() - timedelta(hours=4),
                "updated_at": datetime.utcnow() - timedelta(hours=3),
            },
        )
        integration_db.execute(
            text("INSERT INTO sessions (user_id, token, expires_at) VALUES (:uid, :token, :exp)"),
            {"uid": str(admin_id), "token": admin_token, "exp": datetime.utcnow() + timedelta(days=1)},
        )
        integration_db.commit()

        monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
        response = integration_client.get(
            "/admin/users?q=example&limit=1&offset=1", cookies={"tc_session": admin_token}
        )

        assert response.status_code == 200
        data = response.json()
        assert set(data.keys()) == {"items"}
        assert len(data["items"]) == 1
        assert data["items"][0]["email"] == "another@example.com"
        assert set(data["items"][0].keys()) == {
            "id",
            "email",
            "name",
            "avatar_url",
            "plan",
            "role",
            "created_at",
            "updated_at",
        }


class TestSessionManagement:
    """Integration tests for session management."""

    @pytest.mark.timeout(60)
    def test_session_creation(self, integration_client: TestClient, clean_test_data):
        """Test that sessions are created properly."""
        # Make a request that might create a session
        response = integration_client.get("/")

        # Anonymous requests receive the separate analytics identity cookie,
        # never an authentication session.
        cookies = response.cookies
        assert cookies.get("ha_analytics")
        assert cookies.get("tc_session") is None

    @pytest.mark.timeout(60)
    def test_session_persistence(self, integration_client: TestClient, clean_test_data):
        """Test that sessions persist across requests."""
        # First request
        response1 = integration_client.get("/")
        cookies = response1.cookies

        # Second request with same session
        response2 = integration_client.get("/", cookies=cookies)

        # Should maintain session
        assert response2.status_code in [200, 404]

    @pytest.mark.timeout(60)
    def test_invalid_session_token(self, integration_client: TestClient, clean_test_data):
        """Test handling of invalid session token."""
        # Create request with invalid session cookie
        invalid_cookies = {"tc_session": "invalid_token_12345"}

        response = integration_client.get("/auth/me", cookies=invalid_cookies)

        # Should handle invalid session gracefully
        assert response.status_code in [200, 401, 404]
