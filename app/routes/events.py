import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text as _text

from ..analytics_identity import get_analytics_subject_id
from ..common.session import get_session_token as _get_session_token
from ..common.session import get_user_from_session as _get_user_from_session
from ..db import get_db
from ..event_taxonomy import InvalidAnalyticsEventError, sanitize_client_event

router = APIRouter(prefix="", tags=["Events"])


def _json_payload(value: object) -> str:
    return json.dumps(value if value is not None else {})


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
def ingest_event(payload: dict, request: Request, db=Depends(get_db)):
    """Ingest a client-side event."""
    tok = _get_session_token(request)
    user = _get_user_from_session(db, tok)
    try:
        etype, data = sanitize_client_event(payload.get("type"), payload.get("payload"))
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
def ingest_events_batch(payload: dict, request: Request, db=Depends(get_db)):
    """Ingest multiple client-side events in batch."""
    tok = _get_session_token(request)
    user = _get_user_from_session(db, tok)
    analytics_subject_id = get_analytics_subject_id(request)
    raw_events = payload.get("events") or []
    try:
        sanitized_events = [sanitize_client_event(e.get("type"), e.get("payload")) for e in raw_events]
    except (AttributeError, InvalidAnalyticsEventError) as exc:
        raise HTTPException(status_code=422, detail="invalid analytics event batch") from exc
    for etype, data in sanitized_events:
        db.execute(
            _text(
                "INSERT INTO events (user_id, analytics_subject_id, type, payload) "
                "VALUES (:u,:analytics_subject_id,:ty,CAST(:p AS JSONB))"
            ),
            {
                "u": str(user["id"]) if user else None,
                "analytics_subject_id": analytics_subject_id,
                "ty": etype,
                "p": _json_payload(data),
            },
        )
    db.commit()
    return {"ok": True, "count": len(sanitized_events)}
