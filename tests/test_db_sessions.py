"""Regression coverage for database dependency session isolation."""

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

import app.db as db_module


def test_get_db_creates_independent_sessions_and_close_does_not_cross_contaminate(test_engine, monkeypatch):
    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(bind=test_engine, expire_on_commit=False))
    first_dependency = db_module.get_db()
    second_dependency = db_module.get_db()
    first = next(first_dependency)
    second = next(second_dependency)
    try:
        assert first is not second
        # Force both sessions to check out a connection and start independent
        # database transactions before testing one session's teardown.
        first.execute(text("SELECT 1"))
        second.execute(text("SELECT 1"))
        first_connection = first.connection()
        second_connection = second.connection()
        assert first_connection is not second_connection
        assert first.get_transaction() is not second.get_transaction()

        first.rollback()
        first.close()
        # The second checked-out connection and transaction remain usable.
        assert second.execute(text("SELECT 1")).scalar() == 1
        assert second.is_active
    finally:
        try:
            next(first_dependency)
        except StopIteration:
            pass
        try:
            next(second_dependency)
        except StopIteration:
            pass
