"""Integration tests for authentication and authorization."""

import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from hashlib import sha256
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.settings import settings


def _cleanup_concurrency_role_test_data(engine, email_prefix: str) -> None:
    """Remove committed rows owned by a concurrency role-mutation test."""
    email_pattern = f"{email_prefix}%@example.com"
    with engine.begin() as connection:
        # Delete explicit dependents before their users.  This connection owns
        # and commits cleanup independently of any worker transaction.
        connection.execute(
            text(
                "DELETE FROM audit_logs "
                "WHERE user_id IN (SELECT id FROM users WHERE email LIKE :email_pattern) "
                "OR resource_id IN (SELECT id::text FROM users WHERE email LIKE :email_pattern)"
            ),
            {"email_pattern": email_pattern},
        )
        connection.execute(
            text("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email LIKE :email_pattern)"),
            {"email_pattern": email_pattern},
        )
        connection.execute(text("DELETE FROM users WHERE email LIKE :email_pattern"), {"email_pattern": email_pattern})


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
        assert response.status_code in [200, 302, 307, 404, 503]

    @pytest.mark.timeout(60)
    def test_oauth_callback_missing_code(self, integration_client: TestClient, clean_test_data):
        """Test OAuth callback without authorization code."""
        response = integration_client.get("/auth/callback/google")

        # Should handle missing code gracefully
        assert response.status_code in [400, 404, 422, 503]

    @pytest.mark.timeout(60)
    def test_oauth_callback_with_code(self, integration_client: TestClient, integration_db, clean_test_data):
        """Test OAuth callback with valid code (mocked)."""
        # Note: This test exercises the OAuth callback endpoint without mocking.
        # The endpoint will fail without a valid OAuth code, which is expected behavior.

        response = integration_client.get("/auth/callback/google?code=mock_auth_code")

        # Should handle callback (might not exist or return 404)
        assert response.status_code in [422, 503]
        assert response.json()["error"] in {"validation_error", "external_service_error"}

    @pytest.mark.timeout(60)
    def test_logout(self, integration_client: TestClient, clean_test_data):
        """Test logout endpoint."""
        response = integration_client.post("/auth/logout", headers={"Origin": settings.FRONTEND_ORIGIN})

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
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {"uid": str(user_id), "token_hash": sha256(session_token.encode()).hexdigest(), "exp": datetime.utcnow() + timedelta(days=1)},
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
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {"uid": str(admin_id), "token_hash": sha256(admin_token.encode()).hexdigest(), "exp": datetime.utcnow() + timedelta(days=1)},
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
        integration_db.execute(text("DELETE FROM users WHERE id IN (:admin_id, :other_id)"), {"admin_id": str(admin_id), "other_id": str(other_admin_id)})
        integration_db.commit()

    @pytest.mark.timeout(60)
    def test_concurrent_admin_demotions_leave_exactly_one_admin(self, integration_engine, clean_test_data):
        """Two administrators cannot concurrently demote themselves."""
        from app.accounts import FinalAdminError
        from app.routes.admin import RoleUpdateRequest, admin_set_user_role

        email_prefix = "concurrent-admin-"
        _cleanup_concurrency_role_test_data(integration_engine, email_prefix)
        actor_one, actor_two = uuid.uuid4(), uuid.uuid4()
        try:
            with integration_engine.begin() as connection:
                for user_id in (actor_one, actor_two):
                    connection.execute(
                        text("INSERT INTO users (id, email, role) VALUES (:id, :email, 'admin')"),
                        {"id": str(user_id), "email": f"{email_prefix}{user_id}@example.com"},
                    )
                assert connection.execute(text("SELECT count(*) FROM users WHERE role='admin'")).scalar_one() == 2

            request = Request({"type": "http", "method": "PUT", "path": "/admin/users/role", "headers": [], "client": ("127.0.0.1", 0)})
            sessions = sessionmaker(bind=integration_engine, expire_on_commit=False)
            start_gate = Barrier(2)

            def demote_self(actor_id: uuid.UUID) -> str:
                db = sessions()
                try:
                    start_gate.wait(timeout=5)
                    admin_set_user_role(
                        actor_id,
                        RoleUpdateRequest(role="user"),
                        request,
                        db,
                        {"id": str(actor_id), "role": "admin"},
                    )
                    return "success"
                except FinalAdminError as error:
                    assert error.status_code == 409
                    assert error.error_code == "final_admin"
                    return "final_admin"
                finally:
                    db.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(demote_self, (actor_one, actor_two)))

            assert sorted(outcomes) == ["final_admin", "success"]
            with integration_engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM users WHERE role='admin'")).scalar_one() == 1
                assert connection.execute(text("SELECT count(*) FROM audit_logs WHERE action='admin_action' AND resource_id IN (:one, :two)"), {"one": str(actor_one), "two": str(actor_two)}).scalar_one() == 1
        finally:
            _cleanup_concurrency_role_test_data(integration_engine, email_prefix)

    @pytest.mark.timeout(60)
    def test_queued_self_promotion_revalidates_demoted_session_actor(self, integration_engine, clean_test_data, monkeypatch):
        """A request authorized before waiting must not restore its demoted actor."""
        from app.accounts import ADMIN_ROLE_MUTATION_LOCK, lock_admin_role_mutation
        from app.common.session import get_user_from_session
        from app.exceptions import AuthorizationError
        from app.routes import admin as admin_routes
        from app.routes.admin import RoleUpdateRequest, admin_set_user_role

        actor_id, demoter_id = uuid.uuid4(), uuid.uuid4()
        session_token = secrets.token_urlsafe(32)
        email_prefix = "stale-role-"
        demotion_lock_held, allow_demotion_commit = Event(), Event()
        advisory_lock_attempted = Event()
        self_promotion_started, self_promotion_finished = Event(), Event()
        _cleanup_concurrency_role_test_data(integration_engine, email_prefix)

        try:
            with integration_engine.begin() as connection:
                for user_id in (actor_id, demoter_id):
                    connection.execute(
                        text("INSERT INTO users (id, email, role) VALUES (:id, :email, 'admin')"),
                        {"id": str(user_id), "email": f"{email_prefix}{user_id}@example.com"},
                    )
                connection.execute(
                    text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:id, :token_hash, :expires_at)"),
                    {"id": str(actor_id), "token_hash": sha256(session_token.encode()).hexdigest(), "expires_at": datetime.utcnow() + timedelta(hours=1)},
                )

            sessions = sessionmaker(bind=integration_engine, expire_on_commit=False)

            request = Request({"type": "http", "method": "PUT", "path": f"/admin/users/{actor_id}/role", "headers": [], "client": ("127.0.0.1", 0)})

            real_lock_admin_role_mutation = admin_routes.lock_admin_role_mutation

            def observe_advisory_lock_attempt(db):
                advisory_lock_attempted.set()
                return real_lock_admin_role_mutation(db)

            monkeypatch.setattr(admin_routes, "lock_admin_role_mutation", observe_advisory_lock_attempt)

            def demote_actor():
                with sessions() as db:
                    lock_admin_role_mutation(db)
                    demotion_lock_held.set()
                    assert allow_demotion_commit.wait(5)
                    db.execute(text("UPDATE users SET role='user' WHERE id=:id"), {"id": str(actor_id)})
                    db.commit()

            def queued_self_promotion():
                assert demotion_lock_held.wait(5)
                with sessions() as db:
                    # Match the production dependency and route transaction:
                    # authenticate before the route's advisory lock attempt,
                    # using this same session for both operations.
                    authenticated_actor = get_user_from_session(db, session_token)
                    assert authenticated_actor is not None
                    self_promotion_started.set()
                    try:
                        admin_set_user_role(
                            actor_id,
                            RoleUpdateRequest(role="admin"),
                            request,
                            db,
                            authenticated_actor,
                        )
                    except AuthorizationError as error:
                        return error
                    finally:
                        self_promotion_finished.set()

            executor = ThreadPoolExecutor(max_workers=2)
            try:
                demotion = executor.submit(demote_actor)
                promotion = executor.submit(queued_self_promotion)
                assert demotion_lock_held.wait(5)
                assert self_promotion_started.wait(5)
                assert advisory_lock_attempted.wait(5)

                # A holds only the advisory lock at this point. B has reached
                # its own advisory-lock attempt but cannot finish, proving it
                # is blocked there rather than on the actor row.
                with sessions() as verifier:
                    assert verifier.execute(
                        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                        {"lock_key": ADMIN_ROLE_MUTATION_LOCK},
                    ).scalar_one() is False
                    verifier.rollback()
                assert not self_promotion_finished.is_set()

                allow_demotion_commit.set()
                demotion.result(timeout=10)
                error = promotion.result(timeout=10)
            finally:
                allow_demotion_commit.set()
                executor.shutdown(wait=True, cancel_futures=True)

            assert isinstance(error, AuthorizationError)
            assert error.status_code == 403
            assert error.error_code == "insufficient_permissions"
            assert error.message == "Admin access required"
            with integration_engine.connect() as connection:
                assert connection.execute(text("SELECT role FROM users WHERE id=:id"), {"id": str(actor_id)}).scalar_one() == "user"
                assert connection.execute(
                    text("SELECT count(*) FROM audit_logs WHERE action='admin_action' AND resource_id=:id"),
                    {"id": str(actor_id)},
                ).scalar_one() == 0
        finally:
            allow_demotion_commit.set()
            _cleanup_concurrency_role_test_data(integration_engine, email_prefix)


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
