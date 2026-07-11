"""Tests for database migrations using Alembic.

These tests validate that migrations can be applied and reverted correctly.
"""

import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config


@pytest.fixture(scope="module")
def alembic_config():
    """Create Alembic config for testing."""
    # Get path to alembic.ini in the project root
    alembic_ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    return Config(str(alembic_ini_path))


@pytest.fixture(scope="module")
def test_db_url():
    """Create an isolated database for destructive migration tests."""
    from app.settings import settings

    original_url = settings.DATABASE_URL
    base_url = make_url(
        os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/transcripts")
    )
    admin_url = base_url.set(database="postgres")
    migration_url = base_url.set(database="hasanara_migration_test")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS hasanara_migration_test WITH (FORCE)"))
        conn.execute(text("CREATE DATABASE hasanara_migration_test"))

    settings.DATABASE_URL = migration_url.render_as_string(hide_password=False)
    try:
        yield settings.DATABASE_URL
    finally:
        settings.DATABASE_URL = original_url
        with admin_engine.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS hasanara_migration_test WITH (FORCE)"))
        admin_engine.dispose()


@contextmanager
def get_engine(db_url: str):
    """Context manager for database engine that ensures proper cleanup."""
    engine = create_engine(db_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="function")
def clean_db(test_db_url):
    """Provide a clean database for each test."""
    with get_engine(test_db_url) as engine:
        # The database is dedicated to migration tests, so resetting the schema
        # is both deterministic and safe. It also removes extension-owned
        # objects as a unit instead of trying to drop their functions directly.
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.execute(text("GRANT ALL ON SCHEMA public TO public"))

    yield


def test_migrations_upgrade_head(alembic_config, clean_db, test_db_url):
    """Test that all migrations can be applied successfully."""
    # Run upgrade to head
    command.upgrade(alembic_config, "head")

    # Verify key tables exist
    with get_engine(test_db_url) as engine:
        with engine.begin() as conn:
            # Check that core tables exist
            result = conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
            )
            tables = [row[0] for row in result]

            expected_tables = ["jobs", "videos", "transcripts", "segments", "users", "sessions", "favorites", "events"]
            for table in expected_tables:
                assert table in tables, f"Table {table} should exist after migrations"


def test_migrations_downgrade_base(alembic_config, clean_db, test_db_url):
    """Test that all migrations can be applied and then reverted."""
    # Apply all migrations
    command.upgrade(alembic_config, "head")

    # Downgrade to base
    command.downgrade(alembic_config, "base")

    # Verify tables are removed
    with get_engine(test_db_url) as engine:
        with engine.begin() as conn:
            result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
            tables = [row[0] for row in result]

            # After downgrade, main tables should not exist (only alembic_version might remain)
            unexpected_tables = ["jobs", "videos", "transcripts", "segments"]
            for table in unexpected_tables:
                assert table not in tables, f"Table {table} should not exist after downgrade"


def test_migrations_up_down_up(alembic_config, clean_db, test_db_url):
    """Test that migrations can be applied, reverted, and re-applied."""
    # Upgrade to head
    command.upgrade(alembic_config, "head")

    # Get current revision
    with get_engine(test_db_url) as engine:
        with engine.begin() as conn:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            head_revision = result.scalar()

    # Downgrade one step
    command.downgrade(alembic_config, "-1")

    # Re-upgrade to head
    command.upgrade(alembic_config, "head")

    # Verify we're back at the same revision
    with get_engine(test_db_url) as engine:
        with engine.begin() as conn:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            current_revision = result.scalar()
            assert current_revision == head_revision


def test_migration_history(alembic_config):
    """Test that migration history can be retrieved."""
    # This should not raise an error
    command.history(alembic_config, verbose=True)


def test_current_revision_empty_db(alembic_config, clean_db):
    """Test that current revision shows nothing on empty database."""
    # On an empty database (no migrations applied), current should work but show nothing
    # This should not raise an error
    command.current(alembic_config, verbose=True)


def test_stamp_and_upgrade(alembic_config, clean_db, test_db_url):
    """Test stamping a database and then upgrading."""
    # First upgrade to head
    command.upgrade(alembic_config, "head")

    # Get the current revision
    with get_engine(test_db_url) as engine:
        with engine.begin() as conn:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            revision = result.scalar()

    # Downgrade to base
    command.downgrade(alembic_config, "base")

    # Stamp at the revision we were at
    command.stamp(alembic_config, revision)

    # Verify stamped correctly
    with get_engine(test_db_url) as engine:
        with engine.begin() as conn:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            stamped_revision = result.scalar()
            assert stamped_revision == revision

    # Upgrade should be a no-op since we're already at head
    command.upgrade(alembic_config, "head")
