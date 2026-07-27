"""PostgreSQL-backed advisory-lock lifecycle coverage for the canary protocol."""

import os
import uuid

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.pool import NullPool


def test_server_verified_lock_holder_can_be_terminated_and_reacquired() -> None:
    """Exercise the exact pg_stat_activity/pg_locks and release SQL against test Postgres."""
    database_url = os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/hasanara_test")
    engine = sa.create_engine(database_url, poolclass=NullPool)
    application_name = f"hasanara-diarization-{uuid.uuid4()}"
    assert len(application_name) <= 63
    lock_name = f"hasanara-diarization-lock-test-{uuid.uuid4()}"
    try:
        holder = engine.connect()
        verifier = engine.connect()
        try:
            holder.execute(
                text("SELECT set_config('application_name', :name, false)"),
                {"name": application_name},
            )
            backend_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
            assert holder.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": lock_name}
            ).scalar_one()
            assert verifier.execute(
                text("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_stat_activity a JOIN pg_locks l ON l.pid = a.pid
                    WHERE a.pid = :pid AND a.application_name = :application_name
                      AND l.locktype = 'advisory' AND l.granted
                )
            """),
                {"pid": backend_pid, "application_name": application_name},
            ).scalar_one()
            assert verifier.execute(
                text("""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE pid = :pid AND application_name = :application_name
            """),
                {"pid": backend_pid, "application_name": application_name},
            ).scalar_one()
            holder.invalidate()
        finally:
            holder.close()
            verifier.close()
        with engine.connect() as fresh:
            assert fresh.execute(text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": lock_name}).scalar_one()
            assert fresh.execute(text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": lock_name}).scalar_one()
        with engine.connect() as holder:
            assert holder.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": lock_name}
            ).scalar_one()
        with engine.connect() as fresh:
            assert fresh.execute(text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": lock_name}).scalar_one()
            assert fresh.execute(text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": lock_name}).scalar_one()
    finally:
        engine.dispose()
