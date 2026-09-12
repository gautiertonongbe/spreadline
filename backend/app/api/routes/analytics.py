"""Analytics and provider observability."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import Auth, DbSession, Providers
from app.domains.analytics.service import (
    dashboard,
    opportunity_breakdown,
    prediction_accuracy,
)
from app.models.observations import ProviderRequest

router = APIRouter(tags=["analytics"])


@router.get("/analytics")
def get_analytics(session: DbSession, auth: Auth) -> dict[str, Any]:
    return dashboard(session, auth)


@router.get("/analytics/opportunities")
def get_opportunity_analytics(session: DbSession, auth: Auth) -> dict[str, Any]:
    return opportunity_breakdown(session, auth)


@router.get("/analytics/accuracy")
def get_accuracy(session: DbSession, auth: Auth) -> dict[str, Any]:
    """Predicted versus actual. The platform's report card on itself."""
    return prediction_accuracy(session, auth)


@router.get("/providers")
def list_providers(providers: Providers) -> list[dict[str, Any]]:
    return [
        {
            "slug": info.slug,
            "marketplace": info.marketplace.value,
            "display_name": info.display_name,
            "capabilities": [item.value for item in info.capabilities],
            "is_configured": info.is_configured,
            "is_live": info.is_live,
            "configuration_note": info.configuration_note,
        }
        for info in providers.info()
    ]


@router.get("/providers/health")
def provider_health(providers: Providers, session: DbSession) -> list[dict[str, Any]]:
    """Live reliability metrics per provider (spec §33)."""
    payload: list[dict[str, Any]] = []
    for provider in providers.all():
        metrics = provider.metrics
        payload.append(
            {
                "slug": provider.slug,
                "display_name": provider.display_name,
                "state": metrics.health_state(provider.circuit).value
                if provider.is_configured
                else "not_configured",
                "circuit_state": provider.circuit.state.value,
                "is_live": provider.is_live,
                "is_configured": provider.is_configured,
                "request_count": metrics.request_count,
                "success_count": metrics.success_count,
                "failure_count": metrics.failure_count,
                "success_rate": metrics.success_rate,
                "consecutive_failures": metrics.consecutive_failures,
                "avg_latency_ms": metrics.avg_latency_ms,
                "p95_latency_ms": metrics.p95_latency_ms,
                "rate_limit_per_second": provider.limiter.rate,
                "last_success_at": metrics.last_success_at.isoformat()
                if metrics.last_success_at
                else None,
                "last_failure_at": metrics.last_failure_at.isoformat()
                if metrics.last_failure_at
                else None,
                "last_error": metrics.last_error,
                "configuration_note": provider.configuration_note,
            }
        )
    return payload


@router.get("/providers/requests")
def provider_requests(session: DbSession, limit: int = 100) -> list[dict[str, Any]]:
    """Recent outbound provider calls, for cost and failure inspection."""
    rows = session.scalars(
        select(ProviderRequest).order_by(ProviderRequest.created_at.desc()).limit(limit)
    )
    return [
        {
            "provider": row.provider,
            "capability": row.capability,
            "target": row.target,
            "succeeded": row.succeeded,
            "latency_ms": row.latency_ms,
            "attempts": row.attempts,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
