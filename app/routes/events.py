import json
import time
from collections.abc import Mapping

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text as _text

from ..analytics_identity import get_analytics_subject_id
from ..cache import get_redis_client
from ..common.session import get_session_token as _get_session_token
from ..common.session import get_user_from_session as _get_user_from_session
from ..db import get_db
from ..event_taxonomy import InvalidAnalyticsEventError, sanitize_client_event
from ..logging_config import get_logger
from ..schemas import AnalyticsEventBatchInput, AnalyticsEventInput

router = APIRouter(prefix="", tags=["Events"])
logger = get_logger(__name__)

MAX_EVENT_BYTES = 8 * 1024
MAX_REQUEST_BYTES = 256 * 1024
MAX_PROPERTIES = 32
MAX_NESTED_DEPTH = 2
EVENTS_PER_MINUTE = 120
_event_rate_counts: dict[tuple[str, int], int] = {}


def _json_payload(value: object) -> str:
    return json.dumps(value if value is not None else {})


def _payload_dict(payload: AnalyticsEventInput | Mapping) -> dict:
    return payload.model_dump() if isinstance(payload, AnalyticsEventInput) else dict(payload)


def _depth(value: object, current: int = 0) -> int:
    if isinstance(value, Mapping):
        return max([current, *(_depth(item, current + 1) for item in value.values())])
    if isinstance(value, list):
        return max([current, *(_depth(item, current + 1) for item in value)])
    return current


def _validate_raw_event(event: Mapping) -> None:
    properties = event.get("payload") or {}
    if not isinstance(properties, Mapping):
        raise HTTPException(status_code=422, detail="analytics event payload must be an object")
    if len(properties) > MAX_PROPERTIES:
        raise HTTPException(status_code=422, detail="analytics event has too many properties")
    if _depth(properties) > MAX_NESTED_DEPTH:
        raise HTTPException(status_code=422, detail="analytics event properties are nested too deeply")
    if len(json.dumps(event, separators=(",", ":")).encode()) > MAX_EVENT_BYTES:
        raise HTTPException(status_code=422, detail="analytics event exceeds 8 KiB")


def _rate_limit_key(request: Request) -> str:
    subject = getattr(request.state, "analytics_subject_id", None)
    if subject:
        return f"subject:{subject}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _enforce_event_rate(request: Request, count: int) -> None:
    minute = int(time.time() // 60)
    subject = _rate_limit_key(request)
    redis_client = get_redis_client()
    if redis_client is not None:
        redis_key = f"analytics-rate:{subject}:{minute}"
        used = int(
            redis_client.eval(
                """
                local value = redis.call('INCRBY', KEYS[1], ARGV[1])
                if value == tonumber(ARGV[1]) then redis.call('EXPIRE', KEYS[1], 120) end
                return value
                """,
                1,
                redis_key,
                count,
            )
        )
        if used > EVENTS_PER_MINUTE:
            logger.warning("Rejected analytics event rate limit", extra={"event_count": count})
            raise HTTPException(status_code=429, detail="analytics event rate limit exceeded")
        return

    key = (subject, minute)
    used = _event_rate_counts.get(key, 0)
    if used + count > EVENTS_PER_MINUTE:
        logger.warning("Rejected analytics event rate limit", extra={"event_count": count})
        raise HTTPException(status_code=429, detail="analytics event rate limit exceeded")
    _event_rate_counts[key] = used + count
    for old_key in [item for item in _event_rate_counts if item[1] < minute - 1]:
        del _event_rate_counts[old_key]


@router.post(
    "/events",
    summary="Track client event",
    description="""
    Track a client-side event for analytics.

    Events are stored with optional user association for authenticated users.

    Request body:
    ```json
    {
        "type": "event_type",
        "payload": {"custom": "data"}
    }
    ```
    """,
    responses={200: {"description": "Event tracked", "content": {"application/json": {"example": {"ok": True}}}}},
)
def ingest_event(payload: AnalyticsEventInput, request: Request, db=Depends(get_db)):
    """Ingest a client-side event."""
    raw = _payload_dict(payload)
    _validate_raw_event(raw)
    if len(json.dumps(raw, separators=(",", ":")).encode()) > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="analytics request exceeds 256 KiB")
    _enforce_event_rate(request, 1)
    tok = _get_session_token(request)
    user = _get_user_from_session(db, tok)
    try:
        etype, data = sanitize_client_event(raw.get("type"), raw.get("payload"))
    except InvalidAnalyticsEventError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.execute(
        _text(
            "INSERT INTO events (user_id, analytics_subject_id, type, payload) "
            "VALUES (:u,:analytics_subject_id,:ty,CAST(:p AS JSONB))"
        ),
        {
            "u": str(user["id"]) if user else None,
            "analytics_subject_id": get_analytics_subject_id(request),
            "ty": etype,
            "p": _json_payload(data),
        },
    )
    db.commit()
    return {"ok": True}


@router.post(
    "/events/batch",
    summary="Track multiple events",
    description="""
    Track multiple client-side events in a single request.

    Request body:
    ```json
    {
        "events": [
            {"type": "event1", "payload": {}},
            {"type": "event2", "payload": {}}
        ]
    }
    ```
    """,
    responses={
        200: {
            "description": "Events tracked",
            "content": {"application/json": {"example": {"ok": True, "count": 2}}},
        }
    },
)
def ingest_events_batch(payload: AnalyticsEventBatchInput, request: Request, db=Depends(get_db)):
    """Ingest multiple client-side events in batch."""
    if isinstance(payload, AnalyticsEventBatchInput):
        raw_events = [event.model_dump() for event in payload.events]
    else:
        raw_events = list(payload.get("events") or [])
    if not 1 <= len(raw_events) <= 50:
        raise HTTPException(status_code=422, detail="analytics batch must contain 1 to 50 events")
    request_bytes = len(json.dumps({"events": raw_events}, separators=(",", ":")).encode())
    if request_bytes > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="analytics request exceeds 256 KiB")
    for event in raw_events:
        if not isinstance(event, Mapping):
            raise HTTPException(status_code=422, detail="invalid analytics event batch")
        _validate_raw_event(event)
    _enforce_event_rate(request, len(raw_events))

    tok = _get_session_token(request)
    user = _get_user_from_session(db, tok)
    analytics_subject_id = get_analytics_subject_id(request)
    try:
        sanitized_events = [sanitize_client_event(e.get("type"), e.get("payload")) for e in raw_events]
    except (AttributeError, InvalidAnalyticsEventError) as exc:
        raise HTTPException(status_code=422, detail="invalid analytics event batch") from exc
    records = [{"type": etype, "payload": data} for etype, data in sanitized_events]
    db.execute(
        _text("""
            INSERT INTO events (user_id, analytics_subject_id, type, payload)
            SELECT CAST(:u AS uuid), :analytics_subject_id, item.type, item.payload
            FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS item(type text, payload jsonb)
        """),
        {
            "u": str(user["id"]) if user else None,
            "analytics_subject_id": analytics_subject_id,
            "records": json.dumps(records, separators=(",", ":")),
        },
    )
    db.commit()
    return {"ok": True, "count": len(sanitized_events)}
