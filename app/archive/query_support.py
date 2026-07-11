"""Database execution boundary for archive intelligence queries."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.exceptions import ArchiveUnavailableError
from app.logging_config import get_logger

logger = get_logger(__name__)


def _unavailable(db, operation: str, exc: Exception):
    db.rollback()
    logger.error(
        "Archive intelligence database unavailable",
        extra={"operation": operation, "error_type": type(exc).__name__},
    )
    from app.metrics import archive_unavailable_total

    archive_unavailable_total.labels(operation=operation).inc()
    raise ArchiveUnavailableError(operation) from exc


def query_mappings(db, sql: str, params: dict | None = None):
    try:
        return [dict(row) for row in db.execute(text(sql), params or {}).mappings().all()]
    except (OperationalError, ProgrammingError) as exc:
        return _unavailable(db, "query_many", exc)


def query_scalar(db, sql: str, params: dict | None = None):
    try:
        return db.execute(text(sql), params or {}).scalar_one()
    except (OperationalError, ProgrammingError) as exc:
        return _unavailable(db, "query_scalar", exc)


def execute(db, sql: str, params: dict | None = None):
    try:
        return db.execute(text(sql), params or {})
    except (OperationalError, ProgrammingError) as exc:
        return _unavailable(db, "execute", exc)


def execute_many(db, sql: str, rows: Sequence[dict]):
    if not rows:
        return None
    try:
        return db.execute(text(sql), list(rows))
    except (OperationalError, ProgrammingError) as exc:
        return _unavailable(db, "execute_many", exc)
