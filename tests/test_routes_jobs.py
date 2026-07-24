"""Tests for job routes."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.security import get_user_required


@pytest.fixture
def authenticated_user(db_session):
    user = {
        "id": str(uuid.uuid4()),
        "email": "jobs-user@example.com",
        "name": "Jobs User",
        "plan": "free",
    }
    db_session.execute(
        text(
            "INSERT INTO users (id, email, name, oauth_provider, oauth_subject, plan) "
            "VALUES (:id, :email, :name, 'google', :subject, :plan)"
        ),
        {**user, "subject": f"jobs-{user['id']}"},
    )
    db_session.flush()
    app.dependency_overrides[get_user_required] = lambda: user
    yield user
    app.dependency_overrides.pop(get_user_required, None)


class TestJobsRoutes:
    """Tests for /jobs endpoints."""

    def test_create_job_requires_authentication(self, client: TestClient):
        """Test creating a job without authentication is rejected."""
        response = client.post(
            "/jobs",
            json={"url": "https://youtube.com/watch?v=dQw4w9WgXcQ", "kind": "single"},
        )
        assert response.status_code == 401

    def test_create_job_single_success(self, client: TestClient, authenticated_user):
        """Test creating a single video job successfully."""
        response = client.post(
            "/jobs",
            json={"url": "https://youtube.com/watch?v=dQw4w9WgXcQ", "kind": "single"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["kind"] == "single"
        assert data["state"] in ["pending", "expanded"]

    def test_create_job_channel_success(self, client: TestClient, authenticated_user):
        """Test creating a channel job successfully."""
        response = client.post(
            "/jobs",
            json={"url": "https://youtube.com/channel/UCtest123", "kind": "channel"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["kind"] == "channel"

    def test_create_job_default_kind(self, client: TestClient, authenticated_user):
        """Test creating a job with default kind (single)."""
        response = client.post(
            "/jobs",
            json={"url": "https://youtube.com/watch?v=test456"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["kind"] == "single"

    def test_create_job_invalid_url(self, client: TestClient, authenticated_user):
        """Test creating a job with an invalid URL."""
        response = client.post(
            "/jobs",
            json={"url": "not-a-valid-url", "kind": "single"},
        )
        assert response.status_code == 422  # Validation error

    def test_create_job_missing_url(self, client: TestClient, authenticated_user):
        """Test creating a job without a URL."""
        response = client.post(
            "/jobs",
            json={"kind": "single"},
        )
        assert response.status_code == 422  # Validation error

    def test_get_job_success(self, client: TestClient, authenticated_user):
        """Test getting a job by ID successfully."""
        # First create a job
        create_response = client.post(
            "/jobs",
            json={"url": "https://youtube.com/watch?v=test789", "kind": "single"},
        )
        assert create_response.status_code == 200
        job_id = create_response.json()["id"]

        # Then fetch it
        get_response = client.get(f"/jobs/{job_id}")
        assert get_response.status_code == 200
        data = get_response.json()
        assert data["id"] == job_id
        assert data["kind"] == "single"
        assert "state" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_get_job_not_found(self, client: TestClient, authenticated_user):
        """Test getting a non-existent job."""
        non_existent_id = uuid.uuid4()
        response = client.get(f"/jobs/{non_existent_id}")
        assert response.status_code == 404

    def test_get_job_invalid_uuid(self, client: TestClient, authenticated_user):
        """Test getting a job with an invalid UUID."""
        response = client.get("/jobs/not-a-uuid")
        assert response.status_code == 422  # Validation error

    def test_job_has_required_fields(self, client: TestClient, authenticated_user):
        """Test that a created job has all required fields."""
        response = client.post(
            "/jobs",
            json={"url": "https://youtube.com/watch?v=testfields", "kind": "single"},
        )
        assert response.status_code == 200
        data = response.json()

        # Check all required fields from JobStatus schema
        required_fields = ["id", "kind", "state", "created_at", "updated_at"]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

    def test_job_error_field_nullable(self, client: TestClient, authenticated_user):
        """Test that the error field is nullable for successful jobs."""
        response = client.post(
            "/jobs",
            json={"url": "https://youtube.com/watch?v=testerror", "kind": "single"},
        )
        assert response.status_code == 200
        data = response.json()
        # Error field should be null or not present for new jobs
        assert data.get("error") is None or "error" not in data

    def test_multiple_jobs_different_urls(self, client: TestClient, authenticated_user):
        """Test creating multiple jobs with different URLs."""
        urls = [
            "https://youtube.com/watch?v=test1",
            "https://youtube.com/watch?v=test2",
            "https://youtube.com/watch?v=test3",
        ]
        job_ids = []

        for url in urls:
            response = client.post("/jobs", json={"url": url, "kind": "single"})
            assert response.status_code == 200
            job_ids.append(response.json()["id"])

        # All job IDs should be unique
        assert len(job_ids) == len(set(job_ids))

    def test_duplicate_single_video_job_returns_existing(self, client: TestClient, authenticated_user):
        """Safe duplicate submissions return the existing active job."""
        first = client.post(
            "/jobs",
            json={"url": "https://youtube.com/watch?v=dupe123", "kind": "single"},
        )
        assert first.status_code == 200

        duplicate = client.post(
            "/jobs",
            json={"url": "https://youtu.be/dupe123", "kind": "single"},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == first.json()["id"]

    def test_idempotency_key_returns_original_job(self, client: TestClient, authenticated_user):
        first = client.post(
            "/jobs",
            json={"url": "https://youtube.com/watch?v=idem-one", "idempotency_key": "upload-42"},
        )
        repeated = client.post(
            "/jobs",
            json={"url": "https://youtube.com/watch?v=idem-two", "idempotency_key": "upload-42"},
        )
        assert first.status_code == 200
        assert repeated.status_code == 200
        assert repeated.json()["id"] == first.json()["id"]

    def test_list_and_cancel_are_owner_scoped(self, client: TestClient, authenticated_user):
        created = client.post("/jobs", json={"url": "https://youtube.com/watch?v=owned-job"})
        assert created.status_code == 200

        listed = client.get("/jobs")
        assert [job["id"] for job in listed.json()] == [created.json()["id"]]

        cancelled = client.post(f"/jobs/{created.json()['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["stage"] == "cancelled"
        assert cancelled.json()["cancelled_at"] is not None

        other = dict(authenticated_user, id=str(uuid.uuid4()))
        app.dependency_overrides[get_user_required] = lambda: other
        assert client.get(f"/jobs/{created.json()['id']}").status_code == 404
        assert client.post(f"/jobs/{created.json()['id']}/cancel").status_code == 404

    def test_job_daily_quota_rejected(self, client: TestClient, authenticated_user, monkeypatch):
        """Test per-user job quotas reject excess job creation."""
        monkeypatch.setattr("app.routes.jobs.settings.JOB_CREATE_DAILY_LIMIT", 1)

        first = client.post(
            "/jobs",
            json={"url": "https://youtube.com/watch?v=quota1", "kind": "single"},
        )
        assert first.status_code == 200

        second = client.post(
            "/jobs",
            json={"url": "https://youtube.com/watch?v=quota2", "kind": "single"},
        )
        assert second.status_code == 429
        assert second.json()["error"] == "rate_limit_exceeded"

    def test_channel_job_quota_rejected(self, client: TestClient, authenticated_user, monkeypatch):
        """Test per-user channel-job quotas reject excess channel jobs."""
        monkeypatch.setattr("app.routes.jobs.settings.JOB_CREATE_CHANNEL_DAILY_LIMIT", 1)

        first = client.post(
            "/jobs",
            json={"url": "https://youtube.com/@quota-channel-one", "kind": "channel"},
        )
        assert first.status_code == 200

        second = client.post(
            "/jobs",
            json={"url": "https://youtube.com/@quota-channel-two", "kind": "channel"},
        )
        assert second.status_code == 429
        assert second.json()["error"] == "rate_limit_exceeded"

    def test_batch_expected_jobs_cap_rejected(self, client: TestClient, authenticated_user, monkeypatch):
        """Test oversized staged batch fan-out is rejected."""
        monkeypatch.setattr("app.routes.jobs.settings.JOB_CREATE_MAX_BATCH_EXPECTED_JOBS", 2)
        response = client.post(
            "/jobs",
            json={
                "url": "https://youtube.com/watch?v=batchcap",
                "kind": "single",
                "batch_expected_jobs": 3,
            },
        )
        assert response.status_code == 422
        assert response.json()["error"] == "validation_error"
