from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import settings

# Keep bound values (session tokens and future credentials) out of SQLAlchemy
# logs and exception rendering in every environment.
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, hide_parameters=True)


# A plain sessionmaker creates an independent Session for every call.  Do not
# use scoped_session here: its thread-local registry lets concurrent async
# requests on the same worker share a transaction across awaits.
class SessionmakerWithRemove(sessionmaker[Session]):
    """Independent session factory retaining the former scoped-session hook."""

    def remove(self) -> None:
        """Provide a no-op compatibility hook for legacy callers."""


# Retain the old cleanup hook for existing scripts and test fixtures which
# called ``SessionLocal.remove()`` when this was a scoped_session.
SessionLocal = SessionmakerWithRemove(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
