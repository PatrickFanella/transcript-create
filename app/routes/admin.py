import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import text as _text

from ..accounts import FinalAdminError, lock_admin_role_mutation
from ..audit import ACTION_ADMIN_ACTION, write_audit_from_request
from ..common.session import get_session_token as _get_session_token
from ..common.session import get_user_from_session as _get_user_from_session
from ..common.session import is_admin as _is_admin
from ..csv_export import render_csv
from ..db import get_db
from ..exceptions import AuthorizationError, NotFoundError, ValidationError
from ..security import ROLE_ADMIN, require_role

router = APIRouter(prefix="", tags=["Admin"])


class RoleUpdateRequest(BaseModel):
    role: Literal["user", "moderator", "admin"]


class RoleUpdateResponse(BaseModel):
    user_id: str
    role: Literal["user", "moderator", "admin"]


@router.get("/admin/search/status", summary="Get search indexing freshness (Admin)")
def admin_search_status(db=Depends(get_db), user=Depends(require_role(ROLE_ADMIN))):
    del user
    from app.search.outbox import search_freshness

    return search_freshness(db)


@router.get(
    "/admin/users",
    summary="List users (Admin)",
    description="List users with search and pagination. Admin only.",
    responses={
        200: {"description": "List of users"},
        401: {"description": "Authentication required"},
        403: {"description": "Admin access required"},
    },
)
def admin_users(
    db=Depends(get_db),
    user=Depends(require_role(ROLE_ADMIN)),
    q: str | None = Query(None, description="Search email or name"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of users to return"),
    offset: int = Query(0, ge=0, description="Number of users to skip"),
):
    """List users with search and pagination (admin only)."""
    where: list[str] = []
    params: dict = {"limit": limit, "offset": offset}
    if q:
        where.append("(email ILIKE :q OR COALESCE(name, '') ILIKE :q)")
        params["q"] = f"%{q}%"

    sql = "SELECT id, email, name, avatar_url, plan, role, created_at, updated_at FROM users"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"
    rows = db.execute(_text(sql), params).mappings().all()
    return {"items": [dict(row) for row in rows]}


@router.get(
    "/admin/events",
    summary="List events (Admin)",
    description="""
    List tracked events with filtering and pagination.

    **Admin Only:** Requires admin privileges

    **Filters:**
    - `type`: Filter by event type
    - `user_email`: Filter by user email
    - `start`: Filter events after timestamp
    - `end`: Filter events before timestamp
    """,
    responses={
        200: {"description": "List of events"},
        401: {"description": "Authentication required"},
        403: {"description": "Admin access required"},
    },
)
def admin_events(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role(ROLE_ADMIN)),
    type: str | None = None,
    user_email: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    """List events with filtering (admin only)."""
    where = []
    params: dict = {}
    if type:
        where.append("type = :type")
        params["type"] = type
    if user_email:
        where.append("user_id IN (SELECT id FROM users WHERE email=:email)")
        params["email"] = user_email
    if start:
        where.append("created_at >= :start")
        params["start"] = start
    if end:
        where.append("created_at <= :end")
        params["end"] = end
    sql = "SELECT id, created_at, user_id, analytics_subject_id, type, payload FROM events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
    rows = db.execute(_text(sql), params).mappings().all()
    return {"items": rows}


@router.get(
    "/admin/events.csv",
    summary="Export events as CSV (Admin)",
    description="""
    Export tracked events to CSV format with filtering.

    **Admin Only:** Requires admin privileges
    """,
    responses={
        200: {"description": "CSV file download", "content": {"text/csv": {}}},
        401: {"description": "Authentication required"},
        403: {"description": "Admin access required"},
    },
)
def admin_events_csv(
    request: Request,
    db=Depends(get_db),
    type: str | None = None,
    user_email: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 1000,
    offset: int = 0,
):
    """Export events as CSV (admin only)."""
    user = _get_user_from_session(db, _get_session_token(request))
    if not _is_admin(user):
        raise AuthorizationError("Admin access required")
    where = []
    params: dict = {}
    if type:
        where.append("type = :type")
        params["type"] = type
    if user_email:
        where.append("user_id IN (SELECT id FROM users WHERE email=:email)")
        params["email"] = user_email
    if start:
        where.append("created_at >= :start")
        params["start"] = start
    if end:
        where.append("created_at <= :end")
        params["end"] = end
    sql = "SELECT id, created_at, user_id, analytics_subject_id, type, payload FROM events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
    rows = db.execute(_text(sql), params).all()

    content = render_csv([["id", "created_at", "user_id", "analytics_subject_id", "type", "payload"], *rows])
    return PlainTextResponse(content=content, media_type="text/csv")


@router.get(
    "/admin/events/summary",
    summary="Get events summary (Admin)",
    description="""
    Get summary statistics of tracked events.

    Returns:
    - Event counts grouped by type
    - Event counts grouped by day

    **Admin Only:** Requires admin privileges
    """,
    responses={
        200: {
            "description": "Events summary",
            "content": {
                "application/json": {
                    "example": {
                        "by_type": [{"type": "search", "count": 150}],
                        "by_day": [{"day": "2025-10-25", "count": 42}],
                    }
                }
            },
        },
        401: {"description": "Authentication required"},
        403: {"description": "Admin access required"},
    },
)
def admin_events_summary(request: Request, db=Depends(get_db), start: str | None = None, end: str | None = None):
    """Get event summary statistics (admin only)."""
    user = _get_user_from_session(db, _get_session_token(request))
    if not _is_admin(user):
        raise AuthorizationError("Admin access required")
    params: dict = {}
    where: list[str] = []
    if start:
        where.append("created_at >= :start")
        params["start"] = start
    if end:
        where.append("created_at <= :end")
        params["end"] = end
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    by_type = db.execute(
        _text(f"SELECT type, COUNT(*) FROM events{where_sql} GROUP BY type ORDER BY COUNT(*) DESC"), params
    ).all()
    by_day = db.execute(
        _text(f"SELECT DATE(created_at) as day, COUNT(*) FROM events{where_sql} GROUP BY day ORDER BY day ASC"), params
    ).all()
    return {
        "by_type": [{"type": r[0], "count": r[1]} for r in by_type],
        "by_day": [{"day": str(r[0]), "count": r[1]} for r in by_day],
    }


@router.post(
    "/admin/users/{user_id}/plan",
    summary="Set user plan (Admin)",
    description="""
    Change a user's administratively assigned access plan. Billing is disabled.

    **Admin Only:** Requires admin privileges

    Request body:
    ```json
    {
        "plan": "free"  // or "pro"
    }
    ```
    """,
    responses={
        200: {
            "description": "Plan updated",
            "content": {
                "application/json": {
                    "example": {
                        "ok": True,
                        "user_id": "123e4567-e89b-12d3-a456-426614174000",
                        "plan": "pro",
                    }
                }
            },
        },
        400: {"description": "Invalid plan value"},
        401: {"description": "Authentication required"},
        403: {"description": "Admin access required"},
    },
)
def admin_set_user_plan(user_id: uuid.UUID, payload: dict, request: Request, db=Depends(get_db)):
    """Set a user's plan (admin only)."""
    user = _get_user_from_session(db, _get_session_token(request))
    if not _is_admin(user):
        raise AuthorizationError("Admin access required")
    from ..settings import settings as _settings

    plan = (payload.get("plan") or "").lower()
    if plan not in ("free", _settings.PRO_PLAN_NAME.lower()):
        raise ValidationError(f"Invalid plan. Use 'free' or '{_settings.PRO_PLAN_NAME}'.", field="plan")
    db.execute(_text("UPDATE users SET plan=:p, updated_at=now() WHERE id=:i"), {"p": plan, "i": str(user_id)})
    db.commit()
    return {"ok": True, "user_id": str(user_id), "plan": plan}


@router.put(
    "/admin/users/{user_id}/role",
    summary="Set user role (Admin)",
    response_model=RoleUpdateResponse,
    responses={
        200: {
            "description": "Role updated",
            "content": {
                "application/json": {
                    "example": {"user_id": "123e4567-e89b-12d3-a456-426614174000", "role": "moderator"}
                }
            },
        }
    },
)
def admin_set_user_role(
    user_id: uuid.UUID,
    payload: RoleUpdateRequest,
    request: Request,
    db=Depends(get_db),
    user=Depends(require_role(ROLE_ADMIN)),
):
    """Change a user's durable authorization role in one serialized transaction."""
    try:
        # This lock is shared with account deletion.  It must precede both the
        # target read and admin count so separate target-row demotions cannot
        # strand the archive without an administrator.
        lock_admin_role_mutation(db)
        # The dependency's user was read before the advisory lock was acquired.
        # Revalidate the durable role while holding the same serialization lock,
        # so a queued request cannot act on authorization that was subsequently
        # revoked by an earlier role mutation. Locking the actor first is also
        # safe for self-updates: PostgreSQL permits a transaction to relock its
        # own row.
        actor = (
            db.execute(
                _text("SELECT id, role FROM users WHERE id=:id FOR UPDATE"),
                {"id": str(user["id"])},
            )
            .mappings()
            .first()
        )
        if not actor or actor["role"] != ROLE_ADMIN:
            raise AuthorizationError("Admin access required")
        target = (
            db.execute(
                _text("SELECT id, role FROM users WHERE id=:id FOR UPDATE"),
                {"id": str(user_id)},
            )
            .mappings()
            .first()
        )
        if not target:
            raise NotFoundError("User not found", resource_type="user")

        old_role = target["role"]
        if old_role == ROLE_ADMIN and payload.role != ROLE_ADMIN:
            admin_count = db.execute(_text("SELECT count(*) FROM users WHERE role='admin'")).scalar_one()
            if admin_count == 1:
                raise FinalAdminError()

        db.execute(
            _text("UPDATE users SET role=:role, updated_at=now() WHERE id=:id"),
            {"role": payload.role, "id": str(user_id)},
        )
        write_audit_from_request(
            db,
            request,
            ACTION_ADMIN_ACTION,
            user_id=user["id"],
            resource_type="user",
            resource_id=str(user_id),
            details={"target_user_id": str(user_id), "old_role": old_role, "new_role": payload.role},
        )
        db.commit()
    except (FinalAdminError, NotFoundError):
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return {"user_id": str(user_id), "role": payload.role}
