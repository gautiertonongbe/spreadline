"""Engine, session factory and request-scoped session dependency."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_connect_args: dict[str, object] = {}
_engine_kwargs: dict[str, object] = {
    "echo": settings.database_echo,
    "pool_pre_ping": True,
    "future": True,
}

if settings.is_sqlite:
    # check_same_thread is required because TestClient runs the app on a
    # different thread than the fixture that created the connection.
    _connect_args["check_same_thread"] = False
else:
    _engine_kwargs["pool_size"] = settings.database_pool_size
    _engine_kwargs["max_overflow"] = settings.database_max_overflow

engine: Engine = create_engine(settings.database_url, connect_args=_connect_args, **_engine_kwargs)

if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        # SQLite does not enforce foreign keys unless asked, which would let the
        # test suite pass on data the production database would reject.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts, jobs and tests."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
