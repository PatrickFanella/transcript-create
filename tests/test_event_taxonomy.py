"""Privacy contract for the allowlisted analytics event taxonomy."""

from __future__ import annotations

import pytest


def test_client_event_taxonomy_drops_queries_titles_and_sensitive_keys() -> None:
    from app.event_taxonomy import sanitize_client_event

    event_type, payload = sanitize_client_event(
        "search",
        {
            "q": "person@example.invalid raw-login-token",
            "date_from": "2026-01-01",
            "limit": 20,
            "session_token": "raw-login-token",
            "nested": {"authorization": "Bearer raw-login-token"},
        },
    )

    assert event_type == "search"
    assert payload == {"date_from": "2026-01-01", "limit": 20}
    assert "raw-login-token" not in str(payload)
    assert "example.invalid" not in str(payload)


@pytest.mark.parametrize("event_type", ["unknown", "email=person@example.invalid", "session_token"])
def test_client_event_taxonomy_rejects_unknown_or_identifying_types(event_type: str) -> None:
    from app.event_taxonomy import InvalidAnalyticsEventError, sanitize_client_event

    with pytest.raises(InvalidAnalyticsEventError):
        sanitize_client_event(event_type, {})


def test_event_taxonomy_accepts_only_bounded_scalar_properties() -> None:
    from app.event_taxonomy import sanitize_client_event

    _, payload = sanitize_client_event(
        "result_click",
        {
            "videoId": "11111111-1111-1111-1111-111111111111",
            "start_ms": 1200,
            "id": 41,
            "authorization": "secret",
            "extra": ["not", "scalar"],
        },
    )

    assert payload == {
        "videoId": "11111111-1111-1111-1111-111111111111",
        "start_ms": 1200,
        "id": 41,
    }


def test_credential_shaped_value_is_not_accepted_as_a_video_identifier() -> None:
    from app.event_taxonomy import sanitize_client_event

    _, payload = sanitize_client_event(
        "video_open",
        {"videoId": "raw-login-token", "session_token": "raw-login-token"},
    )

    assert payload == {}


def test_internal_search_event_does_not_store_raw_query() -> None:
    from app.event_taxonomy import sanitize_internal_event

    event_type, payload = sanitize_internal_event(
        "search_api",
        {"q": "person@example.invalid raw-login-token", "source": "native"},
    )

    assert event_type == "search_api"
    assert payload == {"source": "native"}


def test_internal_export_keeps_known_merged_source() -> None:
    from app.event_taxonomy import sanitize_internal_event

    _, payload = sanitize_internal_event(
        "export",
        {
            "video_id": "11111111-1111-1111-1111-111111111111",
            "format": "srt",
            "source": "merged",
            "policy": "best",
        },
    )

    assert payload["source"] == "merged"


def test_historical_unknown_types_use_fixed_other_aggregate_bucket() -> None:
    from app.event_taxonomy import aggregate_event_type

    assert aggregate_event_type("favorite_add") == "favorite_add"
    assert aggregate_event_type("email=person@example.invalid") == "other"
