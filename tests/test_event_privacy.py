"""Analytics event writers and readers never expose login credentials."""

from __future__ import annotations

import csv
import io
import json
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request

from app.routes import admin, events, exports


class FakeResult:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, rows=()):
        self.calls: list[tuple[object, dict | None]] = []
        self.rows = rows

    def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return FakeResult(self.rows)

    def commit(self):
        self.calls.append(("commit", None))


def _request(subject: str = "a" * 64) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/events", "headers": []})
    request.state.analytics_subject_id = subject
    return request


def test_single_event_persists_subject_but_not_login_token(monkeypatch) -> None:
    db = FakeDB()
    user_id = uuid.uuid4()
    monkeypatch.setattr(events, "_get_session_token", lambda _request: "raw-login-token")
    monkeypatch.setattr(events, "_get_user_from_session", lambda _db, _token: {"id": user_id})

    assert events.ingest_event({"type": "favorite_add", "payload": {}}, _request(), db) == {"ok": True}

    statement, params = db.calls[0]
    assert "analytics_subject_id" in str(statement)
    assert "session_token" not in str(statement)
    assert params["analytics_subject_id"] == "a" * 64
    assert "raw-login-token" not in params.values()
    assert params["u"] == str(user_id)


def test_batch_event_persists_one_subject_without_login_token(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(events, "_get_session_token", lambda _request: "raw-login-token")
    monkeypatch.setattr(events, "_get_user_from_session", lambda _db, _token: None)

    result = events.ingest_events_batch(
        {"events": [{"type": "search", "payload": {"q": "housing"}}]},
        _request("b" * 64),
        db,
    )

    assert result == {"ok": True, "count": 1}
    statement, params = db.calls[0]
    assert "session_token" not in str(statement)
    assert params["analytics_subject_id"] == "b" * 64
    assert params["u"] is None
    assert "raw-login-token" not in params.values()


def test_event_route_drops_raw_queries_titles_and_credential_fields(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(events, "_get_session_token", lambda _request: "raw-login-token")
    monkeypatch.setattr(events, "_get_user_from_session", lambda _db, _token: None)

    events.ingest_event(
        {
            "type": "search",
            "payload": {
                "q": "person@example.invalid raw-login-token",
                "date_from": "2026-01-01",
                "session_token": "raw-login-token",
                "nested": {"authorization": "Bearer raw-login-token"},
            },
        },
        _request(),
        db,
    )

    _, params = db.calls[0]
    assert json.loads(params["p"]) == {"date_from": "2026-01-01"}
    assert "raw-login-token" not in params["p"]
    assert "example.invalid" not in params["p"]


def test_batch_rejects_unknown_type_before_any_event_write(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(events, "_get_session_token", lambda _request: "raw-login-token")
    monkeypatch.setattr(events, "_get_user_from_session", lambda _db, _token: None)

    with pytest.raises(HTTPException) as error:
        events.ingest_events_batch(
            {
                "events": [
                    {"type": "search", "payload": {}},
                    {"type": "email=person@example.invalid", "payload": {}},
                ]
            },
            _request(),
            db,
        )

    assert error.value.status_code == 422
    assert db.calls == []


def test_batch_rejects_more_than_fifty_events_before_writing(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(events, "_get_session_token", lambda _request: None)
    monkeypatch.setattr(events, "_get_user_from_session", lambda _db, _token: None)

    with pytest.raises(HTTPException) as error:
        events.ingest_events_batch(
            {"events": [{"type": "search", "payload": {}} for _ in range(51)]},
            _request(),
            db,
        )

    assert error.value.status_code == 422
    assert db.calls == []


def test_batch_uses_one_bulk_insert(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(events, "_get_session_token", lambda _request: None)
    monkeypatch.setattr(events, "_get_user_from_session", lambda _db, _token: None)

    result = events.ingest_events_batch(
        {"events": [{"type": "search", "payload": {}}, {"type": "video_open", "payload": {}}]},
        _request(),
        db,
    )

    assert result == {"ok": True, "count": 2}
    writes = [call for call in db.calls if call[0] != "commit"]
    assert len(writes) == 1


def test_event_rejects_property_count_depth_and_size_before_writing(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(events, "_get_session_token", lambda _request: None)
    monkeypatch.setattr(events, "_get_user_from_session", lambda _db, _token: None)
    monkeypatch.setattr(events, "get_redis_client", lambda: None)

    invalid_payloads = [
        {f"key-{index}": index for index in range(33)},
        {"nested": {"second": {"third": "too deep"}}},
        {"ignored": "x" * (8 * 1024)},
    ]
    for payload in invalid_payloads:
        with pytest.raises(HTTPException) as error:
            events.ingest_event({"type": "search", "payload": payload}, _request(), db)
        assert error.value.status_code == 422
    assert db.calls == []


def test_batch_rejects_request_over_256_kib(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(events, "get_redis_client", lambda: None)
    payload = {"events": [{"type": "search", "payload": {"ignored": f"{index}-" + "x" * 7000}} for index in range(40)]}
    with pytest.raises(HTTPException) as error:
        events.ingest_events_batch(payload, _request(), db)
    assert error.value.status_code == 413
    assert db.calls == []


def test_event_rate_limit_counts_events_not_requests(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(events, "_get_session_token", lambda _request: None)
    monkeypatch.setattr(events, "_get_user_from_session", lambda _db, _token: None)
    monkeypatch.setattr(events, "get_redis_client", lambda: None)
    events._event_rate_counts.clear()
    subject = "r" * 64

    for _ in range(2):
        events.ingest_events_batch(
            {"events": [{"type": "search", "payload": {}} for _ in range(50)]},
            _request(subject),
            db,
        )
    events.ingest_events_batch(
        {"events": [{"type": "search", "payload": {}} for _ in range(20)]},
        _request(subject),
        db,
    )
    with pytest.raises(HTTPException) as error:
        events.ingest_event(
            {"type": "search", "payload": {}},
            _request(subject),
            db,
        )
    assert error.value.status_code == 429


def test_export_event_uses_analytics_subject(monkeypatch) -> None:
    db = FakeDB()
    metric = MagicMock()
    monkeypatch.setattr("app.metrics.exports_total", metric)

    exports._log_export(db, _request("c" * 64), None, {"format": "srt"})

    statement, params = db.calls[0]
    assert "analytics_subject_id" in str(statement)
    assert "session_token" not in str(statement)
    assert params["analytics_subject_id"] == "c" * 64


def test_admin_event_json_and_csv_do_not_select_or_expose_session_token(monkeypatch) -> None:
    row = {
        "id": 1,
        "created_at": "2026-07-10T00:00:00Z",
        "user_id": None,
        "analytics_subject_id": "d" * 64,
        "type": "search",
        "payload": {},
    }
    json_db = FakeDB([row])
    response = admin.admin_events(_request(), json_db, user={"role": "admin"})
    statement, _ = json_db.calls[0]
    assert "session_token" not in str(statement)
    assert response == {"items": [row]}
    assert "session_token" not in response["items"][0]

    csv_db = FakeDB([(1, "2026-07-10T00:00:00Z", None, "d" * 64, "search", {})])
    monkeypatch.setattr(admin, "_get_session_token", lambda _request: "admin-login-token")
    monkeypatch.setattr(admin, "_get_user_from_session", lambda _db, _token: {"role": "admin"})
    monkeypatch.setattr(admin, "_is_admin", lambda _user: True)
    csv_response = admin.admin_events_csv(_request(), csv_db)
    csv_statement, _ = csv_db.calls[0]
    assert "session_token" not in str(csv_statement)
    assert "session_token" not in csv_response.body.decode()
    assert "analytics_subject_id" in csv_response.body.decode().splitlines()[0]


def test_admin_event_csv_uses_csv_writer_and_neutralizes_formulas(monkeypatch) -> None:
    csv_db = FakeDB(
        [
            (
                1,
                "2026-07-10T00:00:00Z",
                None,
                "e" * 64,
                '\t=HYPERLINK("https://example.invalid")',
                {"query": "+SUM(1,1)", "note": 'comma, quote" and newline\n'},
            )
        ]
    )
    monkeypatch.setattr(admin, "_get_session_token", lambda _request: "admin-login-token")
    monkeypatch.setattr(admin, "_get_user_from_session", lambda _db, _token: {"role": "admin"})
    monkeypatch.setattr(admin, "_is_admin", lambda _user: True)

    response = admin.admin_events_csv(_request(), csv_db)
    parsed = list(csv.reader(io.StringIO(response.body.decode())))

    assert parsed[0] == ["id", "created_at", "user_id", "analytics_subject_id", "type", "payload"]
    assert parsed[1][4].startswith("'\t=")
    assert parsed[1][5] == '{"query":"+SUM(1,1)","note":"comma, quote\\" and newline\\n"}'
