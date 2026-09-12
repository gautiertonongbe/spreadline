"""Test fixtures.

The environment is configured before any application module is imported, because
``app.core.config`` builds its settings object at import time and the engine is
created from it.

Each test gets a fresh file-backed SQLite database in a temp directory. A file
rather than ``:memory:`` so that the several connections a request can open all
see the same schema, and fresh per test so no test can depend on another's rows.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="spreadline-tests-"))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_TMP_ROOT / 'default.db'}"
os.environ["ENABLED_PROVIDERS"] = "mock"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["PROVIDER_RATE_LIMIT_PER_SECOND"] = "1000"

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.core.security import AuthContext  # noqa: E402
from app.domains.tenancy.service import bootstrap  # noqa: E402
from app.models import Base  # noqa: E402
from app.services.providers.registry import ProviderRegistry, build_registry  # noqa: E402


@pytest.fixture
def db_path() -> Iterator[Path]:
    path = _TMP_ROOT / f"{uuid.uuid4().hex}.db"
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def engine(db_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:  # type: ignore[no-untyped-def]
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture
def auth(session: Session) -> AuthContext:
    context = bootstrap(session)
    session.commit()
    return context


@pytest.fixture
def registry() -> ProviderRegistry:
    """A fixture-backed provider pair, isolated per test.

    Built fresh each time so reliability state (circuit breaker, metrics) never
    leaks between tests.
    """
    return build_registry(["mock"])


@pytest.fixture
def client(engine, monkeypatch):  # type: ignore[no-untyped-def]
    """A TestClient wired to this test's database."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker as _sessionmaker

    from app.core.database import get_db
    from app.main import create_app

    factory = _sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    # Provider telemetry is flushed outside the request through
    # ``database.session_scope``, which resolves ``SessionLocal`` at call time.
    # Pointing it at this test's engine keeps that real code path exercised
    # rather than silently writing to another database.
    import app.core.database as database_module

    monkeypatch.setattr(database_module, "SessionLocal", factory)

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
