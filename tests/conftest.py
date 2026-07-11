"""Pytest configuration and fixtures for testing."""

import logging
import os
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.pool import NullPool

from app.db import SessionLocal, get_db
from app.middleware import RateLimitMiddleware

# Mock JS runtime validation before importing the app
# This allows tests to run without requiring a JS runtime installed
with patch("app.ytdlp_validation.validate_js_runtime_or_exit"):
    try:
        from app.main import app
    except ImportError as e:
        # Allow worker tests to run without full app dependencies
        app = None
        logging.warning("Could not import app.main (missing dependencies): %s", e)

logger = logging.getLogger(__name__)


def _clear_rate_limit_state() -> None:
    """Keep the process-wide FastAPI app isolated between tests."""
    middleware = getattr(app, "middleware_stack", None) if app is not None else None
    while middleware is not None:
        if isinstance(middleware, RateLimitMiddleware):
            middleware._request_counts.clear()
            middleware._last_cleanup = None
        middleware = getattr(middleware, "app", None)


@pytest.fixture(autouse=True)
def isolate_rate_limit_state():
    """Prevent TestClient's shared synthetic IP from leaking quotas across tests."""
    _clear_rate_limit_state()
    yield
    _clear_rate_limit_state()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Get test database URL from environment."""
    return os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")


@pytest.fixture(scope="session")
def test_engine(test_database_url: str):
    """Create a test database engine."""
    try:
        return create_engine(test_database_url, poolclass=NullPool)
    except ModuleNotFoundError as e:
        # Allow worker tests to run without database driver
        logger.warning("Could not create database engine (missing driver): %s", e)
        return None


@pytest.fixture(scope="function")
def db_session(test_engine) -> Generator:
    """Create a new database session for a test."""
    if test_engine is None:
        pytest.skip("Database engine not available (missing driver)")
    connection = test_engine.connect()
    transaction = connection.begin()

    SessionLocal.remove()
    session = SessionLocal(bind=connection)

    yield session

    session.close()
    SessionLocal.remove()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def client(db_session) -> Generator:
    """Create a test client for the FastAPI app."""
    if app is None:
        pytest.skip("FastAPI app not available (missing dependencies)")

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database(test_engine):
    """Ensure test database schema is set up."""
    if test_engine is None:
        # Skip database setup if engine not available
        yield
        return
    # Check if tables exist by trying to query a core table
    try:
        with test_engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM jobs LIMIT 1"))
    except ProgrammingError:
        # Schema doesn't exist (table not found), but we won't create it here
        # The CI will handle schema setup via docker-compose
        logger.warning("Database schema not found. Ensure schema is initialized before running tests.")
        pass
    except (OperationalError, ModuleNotFoundError) as e:
        # Connection or operational issues - log but don't fail for worker unit tests
        logger.warning("Database connection error (skipping for worker unit tests): %s", e)
        pass

    yield
