"""Hardening checks for analytics retention and its zero-downtime rollout."""

from __future__ import annotations

import os
import re
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from scripts.maintain_event_retention import maintain_event_retention

ROOT = Path(__file__).resolve().parents[1]
FINALIZATION = ROOT / "scripts/finalize_analytics_identity.sh"
RETENTION = ROOT / "scripts/maintain_event_retention.py"
EXPAND_MIGRATION = ROOT / "alembic/versions/20260710_2200_add_analytics_subject_identity.py"
INDEX_MIGRATION = ROOT / "alembic/versions/20260710_2210_add_analytics_subject_index.py"

EXPECTED_EVENT_TYPES = {
    "search",
    "result_click",
    "seek",
    "favorite_add",
    "favorite_remove",
    "video_open",
    "export_click",
    "export",
    "search_api",
}


def _advisory_lock_name(source: str) -> str:
    match = re.search(r"pg_advisory_xact_lock\(hashtext\('([^']+)'\)\)", source)
    assert match is not None
    return match.group(1)


def test_retention_and_finalization_share_one_instant_based_boundary() -> None:
    retention = RETENTION.read_text()
    finalization = FINALIZATION.read_text()

    assert _advisory_lock_name(retention) == _advisory_lock_name(finalization)
    for source in (retention, finalization):
        assert "CURRENT_TIMESTAMP - INTERVAL '90 days'" in source
        assert "CURRENT_DATE - 90" not in source
        assert "ELSE 'other'" in source
        for event_type in EXPECTED_EVENT_TYPES:
            assert f"'{event_type}'" in source

    credential_scrub = "UPDATE events SET session_token = NULL"
    payload_scrub = "UPDATE events SET payload = '{}'::jsonb"
    type_scrub = "UPDATE events\nSET type = 'other'"
    assert credential_scrub in finalization
    assert payload_scrub in finalization
    assert type_scrub in finalization
    assert finalization.index(credential_scrub) < finalization.index(payload_scrub)
    assert finalization.index(credential_scrub) < finalization.index(type_scrub)


def test_compose_retention_exits_on_failure_without_api_healthcheck() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    service = compose.split("    analytics-retention:", 1)[1].split("\n    opensearch:", 1)[0]

    assert "healthcheck:\n            disable: true" in service
    assert "- -euc" in service
    assert "maintain_event_retention.py" in service


def test_subject_index_has_its_own_concurrent_autocommit_migration() -> None:
    expand = EXPAND_MIGRATION.read_text()
    index = INDEX_MIGRATION.read_text()

    assert "create_index" not in expand
    assert 'down_revision: Union[str, None] = "20260710_analytics_identity"' in index
    assert "autocommit_block()" in index
    assert "DROP INDEX CONCURRENTLY IF EXISTS events_analytics_subject_idx" in index
    assert "CREATE INDEX CONCURRENTLY events_analytics_subject_idx" in index
    assert index.index("DROP INDEX CONCURRENTLY") < index.index("CREATE INDEX CONCURRENTLY")
    assert "DROP INDEX CONCURRENTLY IF EXISTS events_analytics_subject_idx" in index


@pytest.mark.skipif(
    not os.environ.get("ANALYTICS_TEST_DATABASE_URL"),
    reason="set ANALYTICS_TEST_DATABASE_URL to run PostgreSQL concurrency coverage",
)
def test_concurrent_retention_runs_do_not_double_count() -> None:
    """Two overlapping maintenance runs preserve each expired event exactly once."""

    schema = f"analytics_retention_{uuid.uuid4().hex}"
    admin_engine = create_engine(os.environ["ANALYTICS_TEST_DATABASE_URL"])
    engine = create_engine(
        os.environ["ANALYTICS_TEST_DATABASE_URL"],
        connect_args={"options": f"-csearch_path={schema}"},
    )
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f"""
                    CREATE TABLE "{schema}".events (
                        id BIGSERIAL PRIMARY KEY,
                        type TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                    );
                    CREATE TABLE "{schema}".event_daily_aggregates (
                        day DATE NOT NULL,
                        type TEXT NOT NULL,
                        count BIGINT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        PRIMARY KEY (day, type)
                    );
                    CREATE FUNCTION "{schema}".delay_aggregate_insert()
                    RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN
                        PERFORM pg_sleep(0.5);
                        RETURN NEW;
                    END
                    $$;
                    CREATE TRIGGER delay_aggregate_insert
                    BEFORE INSERT ON "{schema}".event_daily_aggregates
                    FOR EACH ROW EXECUTE FUNCTION "{schema}".delay_aggregate_insert();
                    INSERT INTO "{schema}".events (type, created_at) VALUES
                        ('search', CURRENT_TIMESTAMP - INTERVAL '91 days'),
                        ('unexpected_legacy_type', CURRENT_TIMESTAMP - INTERVAL '92 days');
                    """))

        barrier = threading.Barrier(2)
        failures: list[BaseException] = []

        def run_retention() -> None:
            try:
                barrier.wait()
                maintain_event_retention(engine)
            except BaseException as exc:  # pragma: no cover - assertion reports thread failures
                failures.append(exc)

        threads = [threading.Thread(target=run_retention) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not failures
        assert all(not thread.is_alive() for thread in threads)
        with engine.begin() as connection:
            aggregates = dict(
                connection.execute(text("SELECT type, count FROM event_daily_aggregates ORDER BY type")).all()
            )
            remaining = connection.execute(text("SELECT COUNT(*) FROM events")).scalar_one()

        assert aggregates == {"other": 1, "search": 1}
        assert remaining == 0
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.skipif(
    not os.environ.get("ANALYTICS_TEST_DATABASE_URL"),
    reason="set ANALYTICS_TEST_DATABASE_URL to run PostgreSQL finalization coverage",
)
def test_finalization_scrubs_historical_payloads_and_unknown_types() -> None:
    """The guarded SQL removes credential-shaped historical event data."""

    schema = f"analytics_finalization_{uuid.uuid4().hex}"
    admin_engine = create_engine(os.environ["ANALYTICS_TEST_DATABASE_URL"])
    engine = create_engine(
        os.environ["ANALYTICS_TEST_DATABASE_URL"],
        connect_args={"options": f"-csearch_path={schema}"},
    )
    sql = FINALIZATION.read_text().split("<<'SQL'\n", 1)[1].split("\nSQL", 1)[0]
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f"""
                    CREATE TABLE "{schema}".events (
                        id BIGSERIAL PRIMARY KEY,
                        analytics_subject_id CHAR(64),
                        session_token TEXT,
                        type TEXT NOT NULL,
                        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE "{schema}".event_daily_aggregates (
                        day DATE NOT NULL,
                        type TEXT NOT NULL,
                        count BIGINT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (day, type)
                    );
                    CREATE TABLE "{schema}".sessions (token TEXT PRIMARY KEY);
                    INSERT INTO "{schema}".events (session_token, type, payload)
                    VALUES ('login-secret', 'legacy_unknown', '{{"credential": "secret"}}'::jsonb);
                    INSERT INTO "{schema}".sessions (token) VALUES ('login-secret');
                    """))

        with engine.begin() as connection:
            connection.exec_driver_sql(sql)
            event = connection.execute(text("SELECT session_token, type, payload FROM events")).one()
            sessions = connection.execute(text("SELECT COUNT(*) FROM sessions")).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO events (session_token, type, payload) "
                    "VALUES ('legacy-write', 'search', '{}'::jsonb)"
                )
            )
            guarded_token = connection.execute(
                text("SELECT session_token FROM events ORDER BY id DESC LIMIT 1")
            ).scalar_one_or_none()

        assert event == (None, "other", {})
        assert sessions == 0
        assert guarded_token is None
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
