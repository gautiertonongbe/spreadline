"""Health and readiness."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.clock import iso_utc, utcnow
from app.core.config import settings

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness. Deliberately does not touch the database."""
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
        "time": iso_utc(utcnow()),
    }


@router.get("/ready")
def ready(session: DbSession) -> dict[str, Any]:
    """Readiness. Fails loudly when the database is unreachable."""
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001 - the message is the payload here
        database = f"error: {type(exc).__name__}"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "time": iso_utc(utcnow()),
    }
