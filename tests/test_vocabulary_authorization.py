import secrets
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text


def _session_user(db, *, email: str, role: str = "user", plan: str = "free") -> tuple[str, str]:
    user_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    db.execute(
        text(
            "INSERT INTO users (id, email, oauth_provider, oauth_subject, role, plan) "
            "VALUES (:id, :email, 'google', :subject, :role, :plan)"
        ),
        {"id": user_id, "email": email, "subject": user_id, "role": role, "plan": plan},
    )
    db.execute(
        text("INSERT INTO sessions (user_id, token, expires_at) VALUES (:uid, :token, :expires)"),
        {"uid": user_id, "token": token, "expires": datetime.utcnow() + timedelta(days=1)},
    )
    db.commit()
    return user_id, token


def _insert_vocabulary(db, *, owner_id: str | None, name: str, is_global: bool = False) -> str:
    vocabulary_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO user_vocabularies (id, user_id, name, terms, is_global) "
            "VALUES (:id, :owner, :name, '[]'::jsonb, :global)"
        ),
        {"id": vocabulary_id, "owner": owner_id, "name": name, "global": is_global},
    )
    db.commit()
    return vocabulary_id


def test_vocabulary_mutations_require_authentication(client: TestClient):
    response = client.post("/vocabularies", json={"name": "Private", "terms": []})
    assert response.status_code == 401

    response = client.delete(f"/vocabularies/{uuid.uuid4()}")
    assert response.status_code == 401


def test_vocabulary_owner_isolation_and_global_visibility(client: TestClient, db_session):
    owner_id, owner_token = _session_user(db_session, email="vocab-owner@example.com")
    other_id, _ = _session_user(db_session, email="vocab-other@example.com")
    global_id = _insert_vocabulary(db_session, owner_id=None, name="Global", is_global=True)
    other_id_value = _insert_vocabulary(db_session, owner_id=other_id, name="Other private")

    created = client.post(
        "/vocabularies",
        json={"name": "Owner private", "terms": [{"pattern": "foo", "replacement": "bar"}]},
        cookies={"tc_session": owner_token},
    )
    assert created.status_code == 201
    created_id = created.json()["id"]
    assert db_session.execute(
        text("SELECT user_id FROM user_vocabularies WHERE id=:id"), {"id": created_id}
    ).scalar_one() == uuid.UUID(owner_id)

    listed = client.get("/vocabularies", cookies={"tc_session": owner_token})
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()} == {created_id, global_id}

    assert client.get(f"/vocabularies/{other_id_value}", cookies={"tc_session": owner_token}).status_code == 404
    assert client.delete(f"/vocabularies/{other_id_value}", cookies={"tc_session": owner_token}).status_code == 404


def test_only_admin_can_manage_global_vocabularies(client: TestClient, db_session):
    _, user_token = _session_user(db_session, email="vocab-user@example.com")
    _, admin_token = _session_user(db_session, email="vocab-admin@example.com", role="admin")

    denied = client.post(
        "/vocabularies",
        json={"name": "Denied global", "terms": [], "is_global": True},
        cookies={"tc_session": user_token},
    )
    assert denied.status_code == 403

    created = client.post(
        "/vocabularies",
        json={"name": "Admin global", "terms": [], "is_global": True},
        cookies={"tc_session": admin_token},
    )
    assert created.status_code == 201
    vocabulary_id = created.json()["id"]
    row = db_session.execute(
        text("SELECT user_id, is_global FROM user_vocabularies WHERE id=:id"), {"id": vocabulary_id}
    ).first()
    assert row == (None, True)
    assert client.delete(f"/vocabularies/{vocabulary_id}", cookies={"tc_session": admin_token}).status_code == 204


def test_auth_me_adds_role_and_capabilities(client: TestClient, db_session):
    _, token = _session_user(db_session, email="capabilities@example.com", role="admin")

    response = client.get("/auth/me", cookies={"tc_session": token})

    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert "admin:access" in response.json()["capabilities"]
    assert response.json()["user"]["email"] == "capabilities@example.com"


def test_job_rejects_inaccessible_vocabulary_ids(client: TestClient, db_session):
    owner_id, owner_token = _session_user(db_session, email="job-vocab-owner@example.com")
    other_id, _ = _session_user(db_session, email="job-vocab-other@example.com")
    accessible_id = _insert_vocabulary(db_session, owner_id=owner_id, name="Mine")
    inaccessible_id = _insert_vocabulary(db_session, owner_id=other_id, name="Not mine")

    rejected = client.post(
        "/jobs",
        json={
            "url": "https://youtube.com/watch?v=vocabulary-access",
            "kind": "single",
            "vocabulary_ids": [accessible_id, inaccessible_id],
        },
        cookies={"tc_session": owner_token},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"] == "validation_error"

    accepted = client.post(
        "/jobs",
        json={
            "url": "https://youtube.com/watch?v=vocabulary-access-ok",
            "kind": "single",
            "vocabulary_ids": [accessible_id],
        },
        cookies={"tc_session": owner_token},
    )
    assert accepted.status_code == 200
