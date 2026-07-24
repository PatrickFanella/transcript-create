"""Tests for security features including RBAC, API keys, and audit logging."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.audit import ACTION_ADMIN_ACTION, ACTION_API_KEY_CREATED, ACTION_API_KEY_REVOKED
from app.csrf import csrf_token
from app.policy import CAP_ADMIN_ACCESS, CAP_MODERATION_ACCESS, CAP_VOCABULARIES_GLOBAL, capabilities_for_role
from app.security import ROLE_ADMIN, ROLE_MODERATOR, ROLE_USER, generate_api_key, get_user_role, has_role, verify_api_key
from app.settings import settings


def _csrf_headers(token: str) -> dict[str, str]:
    return {"Origin": settings.FRONTEND_ORIGIN, "X-CSRF-Token": csrf_token(token)}


class TestRBAC:
    """Tests for Role-Based Access Control."""

    def test_get_user_role_unauthenticated(self):
        """Test role for unauthenticated user."""
        assert get_user_role(None) == ROLE_USER

    def test_get_user_role_regular_user(self):
        """Test role for regular user."""
        user = {"id": str(uuid.uuid4()), "plan": "free", "email": "user@example.com"}
        assert get_user_role(user) == ROLE_USER

    def test_pro_plan_does_not_change_authorization_role(self):
        """A plan entitlement must not elevate authorization privileges."""
        user = {"id": str(uuid.uuid4()), "plan": "pro", "email": "user@example.com"}
        assert get_user_role(user) == ROLE_USER

    def test_stale_pro_role_resolves_to_user(self):
        """Legacy plan values stored in the role column do not grant a role."""
        user = {"id": str(uuid.uuid4()), "role": "pro", "email": "user@example.com"}
        assert get_user_role(user) == ROLE_USER

    def test_moderator_is_between_user_and_admin(self):
        moderator = {"id": str(uuid.uuid4()), "role": "moderator", "plan": "free"}

        assert get_user_role(moderator) == ROLE_MODERATOR
        assert has_role(moderator, ROLE_USER)
        assert has_role(moderator, ROLE_MODERATOR)
        assert not has_role(moderator, ROLE_ADMIN)

    def test_moderator_capabilities_include_moderation_and_global_vocabularies(self):
        capabilities = set(capabilities_for_role(ROLE_MODERATOR))

        assert {CAP_MODERATION_ACCESS, CAP_VOCABULARIES_GLOBAL} <= capabilities
        assert CAP_ADMIN_ACCESS not in capabilities

    def test_has_role_hierarchy(self):
        """Test role hierarchy."""
        user = {"id": str(uuid.uuid4()), "plan": "free", "email": "user@example.com"}
        assert has_role(user, ROLE_USER) is True
        assert has_role(user, ROLE_MODERATOR) is False
        assert has_role(user, ROLE_ADMIN) is False

        pro_user = {"id": str(uuid.uuid4()), "plan": "pro", "email": "pro@example.com"}
        assert has_role(pro_user, ROLE_USER) is True
        assert has_role(pro_user, ROLE_MODERATOR) is False
        assert has_role(pro_user, ROLE_ADMIN) is False

    def test_has_role_rejects_unknown_required_roles(self):
        authenticated_user = {"id": str(uuid.uuid4()), "role": ROLE_ADMIN}

        assert has_role(authenticated_user, "pro") is False
        assert has_role(authenticated_user, "admn") is False

    def test_admin_email_configuration_does_not_elevate_session_user(self, monkeypatch, db_session):
        monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
        user_id = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)

        db_session.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject, role) "
                "VALUES (:id, 'admin@example.com', 'Configured Admin Email', 'google', 'admin-email-user', 'user')"
            ),
            {"id": str(user_id)},
        )
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {"uid": str(user_id), "token_hash": hashlib.sha256(session_token.encode()).hexdigest(), "exp": datetime.utcnow() + timedelta(days=1)},
        )
        db_session.commit()

        from app.common.session import get_user_from_session

        user = get_user_from_session(db_session, session_token)

        assert user is not None
        assert get_user_role(user) == ROLE_USER
        assert set(capabilities_for_role(get_user_role(user))) == set(capabilities_for_role(ROLE_USER))
        assert has_role(user, ROLE_ADMIN) is False

    def test_job_quota_uses_plan_entitlement_not_role(self):
        from app.routes.jobs import _quota_limits_for_user
        from app.settings import settings

        assert _quota_limits_for_user({"id": "user", "role": "user", "plan": "pro"}) == (
            settings.JOB_CREATE_PRO_DAILY_LIMIT,
            settings.JOB_CREATE_PRO_CHANNEL_DAILY_LIMIT,
        )


class TestAPIKeyGeneration:
    """Tests for API key generation and verification."""

    def test_generate_api_key(self):
        """Test API key generation."""
        api_key, api_key_hash = generate_api_key()

        # Check format
        assert api_key.startswith("tc_")
        assert len(api_key) > 10

        # Check hash
        assert len(api_key_hash) == 64  # SHA-256 hex digest

        # Verify hash matches
        # SHA-256 is appropriate for hashing random API keys (not passwords)
        expected_hash = hashlib.sha256(api_key.encode()).hexdigest()  # nosec B324
        assert api_key_hash == expected_hash

    def test_generate_api_key_uniqueness(self):
        """Test that generated API keys are unique."""
        key1, hash1 = generate_api_key()
        key2, hash2 = generate_api_key()

        assert key1 != key2
        assert hash1 != hash2

    def test_verify_api_key_returns_user(self, db_session):
        """Test API key verification returns the owning user."""
        user_id = uuid.uuid4()
        api_key, api_key_hash = generate_api_key()

        db_session.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject) "
                "VALUES (:id, :email, :name, 'google', 'verify-api-key')"
            ),
            {"id": str(user_id), "email": "api-user@example.com", "name": "API User"},
        )
        db_session.execute(
            text(
                "INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix) "
                "VALUES (:id, :uid, :name, :hash, :prefix)"
            ),
            {
                "id": str(uuid.uuid4()),
                "uid": str(user_id),
                "name": "Job Creator",
                "hash": api_key_hash,
                "prefix": api_key[:10] + "...",
            },
        )
        db_session.commit()

        user = verify_api_key(db_session, api_key)

        assert user is not None
        assert str(user["id"]) == str(user_id)
        assert user["api_key_name"] == "Job Creator"


class TestAPIKeyEndpoints:
    """Tests for API key management endpoints."""

    def test_list_api_keys_unauthenticated(self, client: TestClient):
        """Test listing API keys without authentication."""
        response = client.get("/api-keys")
        assert response.status_code == 401

    def test_list_api_keys_empty(self, client: TestClient, db_session):
        """Test listing API keys when none exist."""
        # Create user and session
        user_id = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)

        db_session.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject) "
                "VALUES (:id, :email, :name, 'google', 'test123')"
            ),
            {"id": str(user_id), "email": "test@example.com", "name": "Test User"},
        )
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {"uid": str(user_id), "token_hash": hashlib.sha256(session_token.encode()).hexdigest(), "exp": datetime.utcnow() + timedelta(days=1)},
        )
        db_session.commit()

        response = client.get("/api-keys", cookies={"tc_session": session_token})
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_create_api_key(self, client: TestClient, db_session):
        """Test creating a new API key."""
        # Create user and session
        user_id = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)

        db_session.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject) "
                "VALUES (:id, :email, :name, 'google', 'test123')"
            ),
            {"id": str(user_id), "email": "test@example.com", "name": "Test User"},
        )
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {"uid": str(user_id), "token_hash": hashlib.sha256(session_token.encode()).hexdigest(), "exp": datetime.utcnow() + timedelta(days=1)},
        )
        db_session.commit()

        # Create API key
        response = client.post(
            "/api-keys",
            json={"name": "Test Key", "expires_days": 30},
            cookies={"tc_session": session_token},
            headers=_csrf_headers(session_token),
        )

        assert response.status_code == 201
        data = response.json()

        # Check response structure
        assert "api_key" in data
        assert "key" in data
        assert data["api_key"].startswith("tc_")
        assert data["key"]["name"] == "Test Key"
        assert data["key"]["key_prefix"].startswith("tc_")
        assert set(data["key"]["scopes"].split(",")) == {
            "search:read",
            "videos:read",
            "exports:read",
            "jobs:read",
            "jobs:write",
        }

        # Verify key was stored in database
        stored = (
            db_session.execute(text("SELECT * FROM api_keys WHERE user_id = :uid"), {"uid": str(user_id)})
            .mappings()
            .first()
        )

        assert stored is not None
        assert stored["name"] == "Test Key"

        # Verify audit log
        audit = (
            db_session.execute(
                text("""
                    SELECT * FROM audit_logs
                    WHERE action = :action AND user_id = :uid
                    ORDER BY created_at DESC LIMIT 1
                    """),
                {"action": ACTION_API_KEY_CREATED, "uid": str(user_id)},
            )
            .mappings()
            .first()
        )

        assert audit is not None
        assert audit["success"] is True

    def test_api_key_scope_blocks_job_writes(self, client: TestClient, db_session):
        user_id = uuid.uuid4()
        api_key, api_key_hash = generate_api_key()
        db_session.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject) "
                "VALUES (:id, 'scoped@example.com', 'Scoped', 'google', 'scoped')"
            ),
            {"id": user_id},
        )
        db_session.execute(
            text("""
                INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix, scopes)
                VALUES (:id, :user_id, 'Read only', :hash, :prefix, 'jobs:read')
            """),
            {"id": uuid.uuid4(), "user_id": user_id, "hash": api_key_hash, "prefix": api_key[:10] + "..."},
        )
        db_session.commit()

        response = client.post(
            "/jobs",
            headers={"X-API-Key": api_key},
            json={"url": "https://youtube.com/watch?v=scoped-write"},
        )
        assert response.status_code == 403

    def test_revoke_api_key(self, client: TestClient, db_session):
        """Test revoking an API key."""
        # Create user and session
        user_id = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)
        api_key, api_key_hash = generate_api_key()
        key_id = uuid.uuid4()

        db_session.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject) "
                "VALUES (:id, :email, :name, 'google', 'test123')"
            ),
            {"id": str(user_id), "email": "test@example.com", "name": "Test User"},
        )
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {"uid": str(user_id), "token_hash": hashlib.sha256(session_token.encode()).hexdigest(), "exp": datetime.utcnow() + timedelta(days=1)},
        )
        db_session.execute(
            text(
                "INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix) "
                "VALUES (:id, :uid, :name, :hash, :prefix)"
            ),
            {
                "id": str(key_id),
                "uid": str(user_id),
                "name": "Test Key",
                "hash": api_key_hash,
                "prefix": api_key[:10] + "...",
            },
        )
        db_session.commit()

        # Revoke the key
        response = client.delete(
            f"/api-keys/{key_id}",
            cookies={"tc_session": session_token},
            headers=_csrf_headers(session_token),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

        # Verify key was revoked
        revoked = (
            db_session.execute(text("SELECT revoked_at FROM api_keys WHERE id = :id"), {"id": str(key_id)})
            .mappings()
            .first()
        )

        assert revoked is not None
        assert revoked["revoked_at"] is not None

        # Verify audit log
        audit = (
            db_session.execute(
                text("""
                    SELECT * FROM audit_logs
                    WHERE action = :action AND user_id = :uid
                    ORDER BY created_at DESC LIMIT 1
                    """),
                {"action": ACTION_API_KEY_REVOKED, "uid": str(user_id)},
            )
            .mappings()
            .first()
        )

        assert audit is not None
        assert audit["success"] is True

    def test_revoke_api_key_unauthorized(self, client: TestClient, db_session):
        """Test revoking someone else's API key."""
        # Create two users
        user1_id = uuid.uuid4()
        user2_id = uuid.uuid4()
        session_token = secrets.token_urlsafe(32)
        api_key, api_key_hash = generate_api_key()
        key_id = uuid.uuid4()

        # User 1 owns the key
        db_session.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject) "
                "VALUES (:id, :email, :name, 'google', 'user1')"
            ),
            {"id": str(user1_id), "email": "user1@example.com", "name": "User 1"},
        )

        # User 2 tries to revoke it
        db_session.execute(
            text(
                "INSERT INTO users (id, email, name, oauth_provider, oauth_subject) "
                "VALUES (:id, :email, :name, 'google', 'user2')"
            ),
            {"id": str(user2_id), "email": "user2@example.com", "name": "User 2"},
        )
        db_session.execute(
            text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:uid, :token_hash, :exp)"),
            {"uid": str(user2_id), "token_hash": hashlib.sha256(session_token.encode()).hexdigest(), "exp": datetime.utcnow() + timedelta(days=1)},
        )
        db_session.execute(
            text(
                "INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix) "
                "VALUES (:id, :uid, :name, :hash, :prefix)"
            ),
            {
                "id": str(key_id),
                "uid": str(user1_id),  # Belongs to user1
                "name": "Test Key",
                "hash": api_key_hash,
                "prefix": api_key[:10] + "...",
            },
        )
        db_session.commit()

        # Try to revoke
        response = client.delete(
            f"/api-keys/{key_id}",
            cookies={"tc_session": session_token},
            headers=_csrf_headers(session_token),
        )

        assert response.status_code == 403
        data = response.json()
        assert "permission" in data["message"].lower()

    def test_create_api_key_rolls_back_when_audit_write_fails(self, client: TestClient, test_engine, monkeypatch):
        user_id, session_token = uuid.uuid4(), secrets.token_urlsafe(32)
        with test_engine.begin() as connection:
            connection.execute(text("INSERT INTO users (id, email, role) VALUES (:id, :email, 'user')"), {"id": str(user_id), "email": f"key-create-{user_id}@example.com"})
            connection.execute(text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:id, :hash, now() + interval '1 day')"), {"id": str(user_id), "hash": hashlib.sha256(session_token.encode()).hexdigest()})
        monkeypatch.setattr("app.routes.api_keys.write_audit_from_request", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit failure")))
        client._transport.raise_server_exceptions = False

        response = client.post("/api-keys", json={"name": "will rollback"}, cookies={"tc_session": session_token}, headers=_csrf_headers(session_token))

        assert response.status_code == 500
        with test_engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM api_keys WHERE user_id=:id"), {"id": str(user_id)}).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM audit_logs WHERE user_id=:id AND action=:action"), {"id": str(user_id), "action": ACTION_API_KEY_CREATED}).scalar_one() == 0
        with test_engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": str(user_id)})

    def test_revoke_api_key_rolls_back_when_audit_write_fails(self, client: TestClient, test_engine, monkeypatch):
        user_id, key_id, session_token = uuid.uuid4(), uuid.uuid4(), secrets.token_urlsafe(32)
        with test_engine.begin() as connection:
            connection.execute(text("INSERT INTO users (id, email, role) VALUES (:id, :email, 'user')"), {"id": str(user_id), "email": f"key-revoke-{user_id}@example.com"})
            connection.execute(text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:id, :hash, now() + interval '1 day')"), {"id": str(user_id), "hash": hashlib.sha256(session_token.encode()).hexdigest()})
            connection.execute(text("INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix) VALUES (:key, :user, 'rollback', :hash, 'tc_test...')"), {"key": str(key_id), "user": str(user_id), "hash": hashlib.sha256(str(key_id).encode()).hexdigest()})
        monkeypatch.setattr("app.routes.api_keys.write_audit_from_request", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit failure")))
        client._transport.raise_server_exceptions = False

        response = client.delete(f"/api-keys/{key_id}", cookies={"tc_session": session_token}, headers=_csrf_headers(session_token))

        assert response.status_code == 500
        with test_engine.connect() as connection:
            assert connection.execute(text("SELECT revoked_at IS NULL FROM api_keys WHERE id=:id"), {"id": str(key_id)}).scalar_one()
            assert connection.execute(text("SELECT count(*) FROM audit_logs WHERE user_id=:id AND action=:action"), {"id": str(user_id), "action": ACTION_API_KEY_REVOKED}).scalar_one() == 0
        with test_engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": str(user_id)})


class TestSessionSecurity:
    """Tests for session security features."""

    def test_session_cookie_attributes(self, client: TestClient, db_session):
        """Test that session cookies have secure attributes."""
        # This test would need to inspect Set-Cookie headers
        # For now, we verify the logic in session.py
        from fastapi.responses import Response

        from app.common.session import set_session_cookie

        resp = Response()
        set_session_cookie(resp, "test_token")

        # Check that the cookie is set
        set_cookie_header = resp.headers.get("set-cookie", "")
        assert "tc_session" in set_cookie_header
        assert "HttpOnly" in set_cookie_header
        assert "SameSite=lax" in set_cookie_header


class TestAdminRoleMutation:
    """Role changes must be admin-only, auditable, and all-or-nothing."""

    @staticmethod
    def _create_user(db_session, role: str, token: str | None = None) -> uuid.UUID:
        user_id = uuid.uuid4()
        db_session.execute(
            text("INSERT INTO users (id, email, role) VALUES (:id, :email, :role)"),
            {"id": str(user_id), "email": f"role-{user_id}@example.com", "role": role},
        )
        if token:
            db_session.execute(
                text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:id, :hash, now() + interval '1 day')"),
                {"id": str(user_id), "hash": hashlib.sha256(token.encode()).hexdigest()},
            )
        return user_id

    @pytest.mark.parametrize("role", ["user", "moderator", "admin"])
    def test_admin_can_assign_supported_roles(self, client: TestClient, db_session, role: str):
        token = secrets.token_urlsafe(32)
        admin_id = self._create_user(db_session, "admin", token)
        target_id = self._create_user(db_session, "user")
        db_session.commit()

        response = client.put(f"/admin/users/{target_id}/role", json={"role": role}, cookies={"tc_session": token}, headers=_csrf_headers(token))

        assert response.status_code == 200
        assert response.json() == {"user_id": str(target_id), "role": role}
        assert db_session.execute(text("SELECT role FROM users WHERE id=:id"), {"id": str(target_id)}).scalar_one() == role
        audit = db_session.execute(text("SELECT user_id, resource_id, details FROM audit_logs WHERE action=:action AND resource_id=:id"), {"action": ACTION_ADMIN_ACTION, "id": str(target_id)}).mappings().one()
        assert str(audit["user_id"]) == str(admin_id)
        assert audit["resource_id"] == str(target_id)
        assert audit["details"] == {"target_user_id": str(target_id), "old_role": "user", "new_role": role}
        db_session.execute(text("DELETE FROM users WHERE id IN (:admin_id, :target_id)"), {"admin_id": str(admin_id), "target_id": str(target_id)})
        db_session.commit()

    @pytest.mark.parametrize("actor_role", ["user", "moderator"])
    def test_non_admin_cannot_assign_roles(self, client: TestClient, db_session, actor_role: str):
        token = secrets.token_urlsafe(32)
        actor_id = self._create_user(db_session, actor_role, token)
        target_id = self._create_user(db_session, "user")
        db_session.commit()

        response = client.put(f"/admin/users/{target_id}/role", json={"role": "admin"}, cookies={"tc_session": token}, headers=_csrf_headers(token))

        assert response.status_code == 403
        assert db_session.execute(text("SELECT role FROM users WHERE id=:id"), {"id": str(target_id)}).scalar_one() == "user"
        db_session.execute(text("DELETE FROM users WHERE id IN (:actor_id, :target_id)"), {"actor_id": str(actor_id), "target_id": str(target_id)})
        db_session.commit()

    def test_rejects_unknown_role_and_nonexistent_target(self, client: TestClient, db_session):
        token = secrets.token_urlsafe(32)
        admin_id = self._create_user(db_session, "admin", token)
        db_session.commit()

        invalid = client.put(f"/admin/users/{uuid.uuid4()}/role", json={"role": "owner"}, cookies={"tc_session": token}, headers=_csrf_headers(token))
        missing = client.put(f"/admin/users/{uuid.uuid4()}/role", json={"role": "user"}, cookies={"tc_session": token}, headers=_csrf_headers(token))

        assert invalid.status_code == 422
        assert missing.status_code == 404
        db_session.execute(text("DELETE FROM users WHERE id=:id"), {"id": str(admin_id)})
        db_session.commit()

    def test_final_admin_demotion_rolls_back_without_audit(self, client: TestClient, test_engine):
        token = secrets.token_urlsafe(32)
        admin_id = uuid.uuid4()
        with test_engine.begin() as connection:
            connection.execute(text("INSERT INTO users (id, email, role) VALUES (:id, :email, 'admin')"), {"id": str(admin_id), "email": f"final-admin-{admin_id}@example.com"})
            connection.execute(text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:id, :hash, now() + interval '1 day')"), {"id": str(admin_id), "hash": hashlib.sha256(token.encode()).hexdigest()})

        response = client.put(f"/admin/users/{admin_id}/role", json={"role": "user"}, cookies={"tc_session": token}, headers=_csrf_headers(token))

        assert response.status_code == 409
        assert response.json()["error"] == "final_admin"
        with test_engine.connect() as connection:
            assert connection.execute(text("SELECT role FROM users WHERE id=:id"), {"id": str(admin_id)}).scalar_one() == "admin"
            assert connection.execute(text("SELECT count(*) FROM audit_logs WHERE action=:action AND resource_id=:id"), {"action": ACTION_ADMIN_ACTION, "id": str(admin_id)}).scalar_one() == 0
        with test_engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": str(admin_id)})

    def test_role_change_rolls_back_when_audit_write_fails(self, client: TestClient, test_engine, monkeypatch):
        token = secrets.token_urlsafe(32)
        admin_id, target_id = uuid.uuid4(), uuid.uuid4()
        with test_engine.begin() as connection:
            for user_id, role in ((admin_id, "admin"), (target_id, "user")):
                connection.execute(text("INSERT INTO users (id, email, role) VALUES (:id, :email, :role)"), {"id": str(user_id), "email": f"audit-role-{user_id}@example.com", "role": role})
            connection.execute(text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:id, :hash, now() + interval '1 day')"), {"id": str(admin_id), "hash": hashlib.sha256(token.encode()).hexdigest()})
        monkeypatch.setattr("app.routes.admin.write_audit_from_request", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit failure")))
        client._transport.raise_server_exceptions = False

        response = client.put(f"/admin/users/{target_id}/role", json={"role": "moderator"}, cookies={"tc_session": token}, headers=_csrf_headers(token))

        assert response.status_code == 500
        with test_engine.connect() as connection:
            assert connection.execute(text("SELECT role FROM users WHERE id=:id"), {"id": str(target_id)}).scalar_one() == "user"
            assert connection.execute(text("SELECT count(*) FROM audit_logs WHERE action=:action AND resource_id=:id"), {"action": ACTION_ADMIN_ACTION, "id": str(target_id)}).scalar_one() == 0
        with test_engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id IN (:admin_id, :target_id)"), {"admin_id": str(admin_id), "target_id": str(target_id)})


def test_new_job_write_keeps_column_and_json_owner_authoritative(db_session):
    from app.routes.jobs import JobCreate, create_job

    user_id = uuid.uuid4()
    db_session.execute(text("INSERT INTO users (id, email, role) VALUES (:id, :email, 'user')"), {"id": str(user_id), "email": f"job-owner-{user_id}@example.com"})

    job = create_job(JobCreate(url=f"https://youtube.com/watch?v=owner{uuid.uuid4().hex}"), db=db_session, user={"id": str(user_id), "role": "user"})

    row = db_session.execute(text("SELECT owner_user_id, meta FROM jobs WHERE id=:id"), {"id": str(job.id)}).mappings().one()
    assert str(row["owner_user_id"]) == str(user_id)
    assert row["meta"].get("owner_user_id") == str(user_id)


class TestAuditLogging:
    """Tests for audit logging functionality."""

    def test_audit_log_creation(self, db_session, test_engine):
        """Test creating audit log entries."""
        from app.audit import log_audit_event

        user_id = uuid.uuid4()
        # log_audit_event deliberately uses a separate transaction, so its
        # referenced user must be visible before it is called.
        with test_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, oauth_provider, oauth_subject) "
                    "VALUES (:id, :email, 'google', :subject)"
                ),
                {"id": user_id, "email": f"audit-{user_id}@example.com", "subject": f"audit-{user_id}"},
            )
        log_audit_event(
            db_session,
            action="test_action",
            user_id=user_id,
            success=True,
            details={"test": "data"},
        )

        # Verify log was created
        with test_engine.connect() as connection:
            log = (
                connection.execute(text("SELECT * FROM audit_logs WHERE user_id = :uid"), {"uid": str(user_id)})
                .mappings()
                .one()
            )

        assert log is not None
        assert log["action"] == "test_action"
        assert log["success"] is True
        assert log["details"]["test"] == "data"
        with test_engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": str(user_id)})

    def test_standalone_audit_failure_is_best_effort_and_does_not_finalize_caller_transaction(self, monkeypatch):
        from unittest.mock import MagicMock

        import app.audit as audit

        caller = MagicMock()
        independent = MagicMock()
        independent.execute.side_effect = RuntimeError("audit storage unavailable")
        monkeypatch.setattr(audit, "SessionLocal", lambda: independent)

        audit.log_audit_event(caller, action="test_action")

        caller.commit.assert_not_called()
        caller.rollback.assert_not_called()
        independent.rollback.assert_called_once()
        independent.close.assert_called_once()
