"""Application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analytics, bulk, health, opportunities, portfolio, products, simulate
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.services.providers import telemetry
from app.services.providers.registry import build_registry, set_registry
from app.services.scheduling.base import build_scheduler
from app.services.scheduling.jobs import register_jobs

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    configure_logging()

    registry = build_registry(on_call=telemetry.record)
    set_registry(registry)
    logger.info(
        "providers registered",
        extra={
            "context": {
                "providers": [
                    {
                        "slug": info.slug,
                        "configured": info.is_configured,
                        "live": info.is_live,
                    }
                    for info in registry.info()
                ]
            }
        },
    )

    scheduler = register_jobs(build_scheduler())
    scheduler.start()
    app.state.scheduler = scheduler
    app.state.providers = registry

    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            # Provider URLs and request bodies can carry identifiers; no PII is
            # attached by default.
            send_default_pii=False,
        )

    try:
        yield
    finally:
        scheduler.shutdown()
        telemetry.flush_to_database()
        await registry.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Cross-market inventory intelligence. Spreadline does not merely find "
            "price differences; it determines whether the spread represents a good "
            "inventory decision."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    register_exception_handlers(app)

    @app.middleware("http")
    async def persist_provider_telemetry(request, call_next):  # type: ignore[no-untyped-def]
        """Write buffered provider calls after the request's session is closed.

        Doing this inside the request would open a second writer against the
        transaction still in flight. Here the endpoint has returned, its session
        dependency has been torn down, and the insert is a clean single batch.
        """
        response = await call_next(request)
        telemetry.flush_to_database()
        return response

    # Versioned from the first release: API contracts are meant to outlive the
    # client that first used them.
    api = APIRouter(prefix=settings.api_v1_prefix)
    api.include_router(health.router)
    api.include_router(products.router)
    api.include_router(opportunities.router)
    api.include_router(bulk.router)
    api.include_router(simulate.router)
    api.include_router(portfolio.router)
    api.include_router(analytics.router)
    app.include_router(api)

    # Unversioned liveness, for load balancers that will not be told about /api/v1.
    app.include_router(health.router)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, Any]:
        return {
            "service": settings.app_name,
            "version": "0.1.0",
            "docs": "/docs",
            "api": settings.api_v1_prefix,
        }

    return app


app = create_app()
