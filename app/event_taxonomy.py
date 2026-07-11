"""Allowlisted, non-identifying analytics event taxonomy."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Callable, Mapping
from typing import Any

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class InvalidAnalyticsEventError(ValueError):
    """Raised when a producer submits an event outside the fixed taxonomy."""


def _uuid_identifier(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


def _date(value: object) -> bool:
    return isinstance(value, str) and bool(_ISO_DATE.fullmatch(value))


def _non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2_147_483_647


def _non_negative_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 2_147_483_647
    )


def _event_id(value: object) -> bool:
    return _uuid_identifier(value) or _non_negative_int(value)


def _one_of(*values: str) -> Callable[[object], bool]:
    allowed = frozenset(values)
    return lambda value: isinstance(value, str) and value in allowed


_CLIENT_PROPERTIES: dict[str, dict[str, Callable[[object], bool]]] = {
    "search": {
        "date_from": _date,
        "date_to": _date,
        "video_id": _uuid_identifier,
        "limit": _non_negative_int,
        "offset": _non_negative_int,
    },
    "result_click": {"videoId": _uuid_identifier, "start_ms": _non_negative_int, "id": _event_id},
    "seek": {"videoId": _uuid_identifier, "seconds": _non_negative_number},
    "favorite_add": {"videoId": _uuid_identifier, "start_ms": _non_negative_int},
    "favorite_remove": {"videoId": _uuid_identifier, "start_ms": _non_negative_int},
    "video_open": {"videoId": _uuid_identifier},
    "export_click": {
        "videoId": _uuid_identifier,
        "format": _one_of("srt", "vtt", "json", "pdf", "csv", "m3u"),
        "source": _one_of("best", "native", "youtube", "whisper", "merged"),
    },
}

_INTERNAL_PROPERTIES: dict[str, dict[str, Callable[[object], bool]]] = {
    "export": {
        "video_id": _uuid_identifier,
        "format": _one_of("srt", "vtt", "json", "pdf", "csv", "m3u"),
        "source": _one_of("best", "native", "youtube", "whisper", "merged"),
        "policy": _one_of("best"),
    },
    "search_api": {"source": _one_of("best", "native", "youtube", "whisper", "opensearch", "postgres")},
}

AGGREGATE_EVENT_TYPES = frozenset((*_CLIENT_PROPERTIES, *_INTERNAL_PROPERTIES))


def _sanitize(
    event_type: object,
    payload: object,
    taxonomy: Mapping[str, Mapping[str, Callable[[object], bool]]],
) -> tuple[str, dict[str, Any]]:
    if not isinstance(event_type, str) or event_type not in taxonomy:
        raise InvalidAnalyticsEventError("unsupported analytics event type")
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise InvalidAnalyticsEventError("analytics event payload must be an object")

    validators = taxonomy[event_type]
    sanitized = {key: value for key, value in payload.items() if key in validators and validators[key](value)}
    return event_type, sanitized


def sanitize_client_event(event_type: object, payload: object) -> tuple[str, dict[str, Any]]:
    """Validate a browser event and retain only bounded non-identifying fields."""

    return _sanitize(event_type, payload, _CLIENT_PROPERTIES)


def sanitize_internal_event(event_type: object, payload: object) -> tuple[str, dict[str, Any]]:
    """Validate an application-generated analytics event."""

    return _sanitize(event_type, payload, _INTERNAL_PROPERTIES)


def aggregate_event_type(event_type: str) -> str:
    """Map legacy/unknown types into the fixed non-identifying aggregate bucket."""

    return event_type if event_type in AGGREGATE_EVENT_TYPES else "other"
