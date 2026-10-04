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
# The test client speaks plain HTTP, and a Secure cookie is never sent over it.
# Left at the production default this produces a login that returns 200 and a
# client that is not signed in, which is exactly the failure a real plain-HTTP
# deployment would see.
os.environ["SECURE_COOKIES"] = "false"

from sqlalchemy import create_engine, event  # noqa: E402
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

    # The same pragma the application engine sets. This fixture builds its own
    # engine rather than reusing that one, and for a long time it did so without
    # this listener, so every test ran with foreign keys unenforced while
    # production enforced them. The suite could then pass on rows the real
    # database would reject, which is the one difference between a test engine
    # and a production engine that must never exist.
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:  # type: ignore[no-untyped-def]
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


#: The account every API test signs in as. A real password through the real
#: login route: a fixture that forged a session would test a path no browser
#: ever takes.
TEST_EMAIL = "operator@spreadline.test"
TEST_PASSWORD = "correct-horse-battery"


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

    # The API requires a session, so the fixture creates an account and signs in
    # the way a browser does. Every test below therefore exercises the real
    # authentication path rather than a bypass, and a test that needs to see an
    # unauthenticated response clears the cookies itself.
    from app.domains.tenancy.auth import create_user
    from app.domains.tenancy.service import ensure_default_organization

    bootstrap_session = factory()
    try:
        organization = ensure_default_organization(bootstrap_session)
        create_user(
            bootstrap_session,
            organization,
            email=TEST_EMAIL,
            password=TEST_PASSWORD,
            full_name="Test Operator",
            role="owner",
        )
        bootstrap_session.commit()
    finally:
        bootstrap_session.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        )
        assert response.status_code == 200, response.text
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client(client):  # type: ignore[no-untyped-def]
    """The same client with its session thrown away.

    For asserting what an unauthenticated caller sees, which is the assertion
    that matters most and the one a signed-in fixture would hide.
    """
    client.cookies.clear()
    return client
