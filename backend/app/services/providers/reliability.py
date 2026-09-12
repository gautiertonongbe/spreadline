"""Provider reliability: timeouts, retries, backoff, circuit breaking, rate
limiting and health recording.

Wrapped around every provider so that no domain has to think about it. The proxy
implements the same interface as the provider it wraps, so callers cannot tell
the difference and cannot bypass it.

Failure policy, deliberately narrow: only transport-shaped failures are retried.
A capability error, a missing credential or a not-found is a permanent answer, and
retrying it three times with backoff just burns quota to arrive at the same place.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.core.clock import utcnow
from app.core.config import settings
from app.core.errors import (
    NotFoundError,
    ProviderCapabilityError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
)
from app.core.logging import get_logger
from app.models.enums import CircuitState, ProviderHealthState
from app.services.providers.base import MarketplaceProvider, ProviderCapability

logger = get_logger(__name__)

T = TypeVar("T")

#: Errors that mean "this request will never succeed", so they bypass retries and
#: do not count towards the circuit breaker.
PERMANENT_ERRORS = (ProviderCapabilityError, ProviderNotConfiguredError, NotFoundError)


class RateLimiter:
    """Token bucket. Smooths bursts instead of rejecting them."""

    def __init__(self, rate_per_second: float, *, burst: float | None = None):
        self.rate = max(rate_per_second, 0.001)
        self.capacity = burst if burst is not None else max(1.0, rate_per_second)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Wait until a token is free. Returns how long it waited, in seconds."""
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
            self._updated = now
            if self._tokens >= 1:
                self._tokens -= 1
                return 0.0
            wait = (1 - self._tokens) / self.rate
        await asyncio.sleep(wait)
        async with self._lock:
            self._tokens = max(0.0, self._tokens - 1)
            self._updated = time.monotonic()
        return wait


@dataclass
class CircuitBreaker:
    """Stops calling a provider that is failing, and probes before resuming."""

    failure_threshold: int = 5
    reset_seconds: float = 60.0
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None

    def allows_request(self) -> bool:
        if self.state is CircuitState.CLOSED:
            return True
        if self.state is CircuitState.OPEN:
            if self.opened_at is None or (time.monotonic() - self.opened_at) >= self.reset_seconds:
                # One probe is allowed through; its outcome decides the circuit.
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN: the probe itself

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if (
            self.state is CircuitState.HALF_OPEN
            or self.consecutive_failures >= self.failure_threshold
        ):
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()


@dataclass
class ProviderMetrics:
    """In-process rolling metrics. Persisted separately by the health service."""

    provider: str
    request_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    total_latency_ms: int = 0
    latencies_ms: list[int] = field(default_factory=list)
    last_success_at: Any = None
    last_failure_at: Any = None
    last_error: str | None = None
    rate_limited_waits: int = 0

    def record(self, *, success: bool, latency_ms: int, error: str | None = None) -> None:
        self.request_count += 1
        self.total_latency_ms += latency_ms
        # A bounded window keeps the percentile meaningful and the memory flat.
        self.latencies_ms.append(latency_ms)
        if len(self.latencies_ms) > 500:
            del self.latencies_ms[:-500]
        if success:
            self.success_count += 1
            self.consecutive_failures = 0
            self.last_success_at = utcnow()
        else:
            self.failure_count += 1
            self.consecutive_failures += 1
            self.last_failure_at = utcnow()
            self.last_error = error

    @property
    def success_rate(self) -> float | None:
        if not self.request_count:
            return None
        return round(self.success_count / self.request_count, 4)

    @property
    def avg_latency_ms(self) -> int | None:
        if not self.request_count:
            return None
        return round(self.total_latency_ms / self.request_count)

    @property
    def p95_latency_ms(self) -> int | None:
        if not self.latencies_ms:
            return None
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return ordered[index]

    def health_state(self, circuit: CircuitBreaker) -> ProviderHealthState:
        if circuit.state is CircuitState.OPEN:
            return ProviderHealthState.UNAVAILABLE
        if not self.request_count:
            return ProviderHealthState.HEALTHY
        rate = self.success_rate or 0
        if rate < 0.5 or self.consecutive_failures >= 3:
            return ProviderHealthState.UNAVAILABLE
        if rate < 0.9 or self.consecutive_failures >= 1:
            return ProviderHealthState.DEGRADED
        return ProviderHealthState.HEALTHY


@dataclass
class CallRecord:
    """One completed call, for persistence by the caller."""

    provider: str
    capability: str
    target: str | None
    succeeded: bool
    latency_ms: int
    attempts: int
    error_code: str | None = None
    error_message: str | None = None


class ReliableProvider(MarketplaceProvider):
    """Decorates a provider with the full reliability policy.

    Presents the wrapped provider's identity and capabilities so that it is a
    drop-in substitute.
    """

    def __init__(
        self,
        provider: MarketplaceProvider,
        *,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        rate_limit_per_second: float | None = None,
        on_call: Callable[[CallRecord], None] | None = None,
    ):
        self._provider = provider
        self.slug = provider.slug
        self.marketplace = provider.marketplace
        self.display_name = provider.display_name
        self.capabilities = provider.capabilities
        self.is_live = provider.is_live
        self.timeout_seconds = timeout_seconds or settings.provider_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else settings.provider_max_retries
        self.limiter = RateLimiter(rate_limit_per_second or settings.provider_rate_limit_per_second)
        self.circuit = CircuitBreaker(
            failure_threshold=settings.provider_circuit_failure_threshold,
            reset_seconds=settings.provider_circuit_reset_seconds,
        )
        self.metrics = ProviderMetrics(provider=provider.slug)
        self._on_call = on_call

    @property
    def is_configured(self) -> bool:
        return self._provider.is_configured

    @property
    def configuration_note(self) -> str | None:
        return self._provider.configuration_note

    @property
    def wrapped(self) -> MarketplaceProvider:
        return self._provider

    async def _call(
        self,
        capability: ProviderCapability,
        target: str | None,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        if not self._provider.is_configured:
            raise ProviderNotConfiguredError(
                f"{self.display_name} has no credentials configured.",
                provider=self.slug,
                capability=capability.value,
            )
        if not self.circuit.allows_request():
            raise ProviderUnavailableError(
                f"{self.display_name} is temporarily unavailable "
                f"(circuit open after {self.circuit.consecutive_failures} failures).",
                provider=self.slug,
                capability=capability.value,
            )

        attempts = 0
        started = time.monotonic()
        last_error: Exception | None = None

        while attempts <= self.max_retries:
            attempts += 1
            await self.limiter.acquire()
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    result = await operation()
            except PERMANENT_ERRORS as exc:
                # Not a reliability event: the provider answered definitively.
                self._finish(capability, target, started, attempts, error=exc, count=False)
                raise
            except TimeoutError as exc:
                last_error = ProviderError(
                    f"{self.display_name} timed out after {self.timeout_seconds}s.",
                    provider=self.slug,
                )
                last_error.__cause__ = exc
            except Exception as exc:  # noqa: BLE001 - provider transports vary
                last_error = exc
            else:
                self.circuit.record_success()
                self._finish(capability, target, started, attempts)
                return result

            if attempts > self.max_retries:
                break
            # Exponential backoff with full jitter: synchronised retries from
            # several workers are how a degraded provider is turned into a
            # dead one.
            delay = min(
                settings.provider_backoff_max_seconds,
                settings.provider_backoff_base_seconds * (2 ** (attempts - 1)),
            )
            await asyncio.sleep(random.uniform(0, delay))  # noqa: S311 - jitter, not crypto

        self.circuit.record_failure()
        self._finish(capability, target, started, attempts, error=last_error)
        message = str(last_error) if last_error else "unknown provider failure"
        logger.warning(
            "provider call failed",
            extra={
                "context": {
                    "provider": self.slug,
                    "capability": capability.value,
                    "attempts": attempts,
                    "error": message,
                }
            },
        )
        raise ProviderError(
            f"{self.display_name} failed after {attempts} attempt(s): {message}",
            provider=self.slug,
            capability=capability.value,
        )

    def _finish(
        self,
        capability: ProviderCapability,
        target: str | None,
        started: float,
        attempts: int,
        *,
        error: Exception | None = None,
        count: bool = True,
    ) -> None:
        latency_ms = int((time.monotonic() - started) * 1000)
        if count:
            self.metrics.record(
                success=error is None, latency_ms=latency_ms, error=str(error) if error else None
            )
        if self._on_call:
            self._on_call(
                CallRecord(
                    provider=self.slug,
                    capability=capability.value,
                    target=target,
                    succeeded=error is None,
                    latency_ms=latency_ms,
                    attempts=attempts,
                    error_code=type(error).__name__ if error else None,
                    error_message=str(error)[:500] if error else None,
                )
            )

    # -- delegated capability methods --------------------------------------

    async def search_products(self, query: str, *, limit: int = 20):
        return await self._call(
            ProviderCapability.SEARCH,
            query,
            lambda: self._provider.search_products(query, limit=limit),
        )

    async def get_product(self, external_id: str):
        return await self._call(
            ProviderCapability.PRODUCT, external_id, lambda: self._provider.get_product(external_id)
        )

    async def get_product_by_identifier(self, identifier_type: str, value: str):
        return await self._call(
            ProviderCapability.PRODUCT,
            f"{identifier_type}:{value}",
            lambda: self._provider.get_product_by_identifier(identifier_type, value),
        )

    async def get_price(self, external_id: str):
        return await self._call(
            ProviderCapability.PRICE, external_id, lambda: self._provider.get_price(external_id)
        )

    async def get_offers(self, external_id: str):
        return await self._call(
            ProviderCapability.OFFERS, external_id, lambda: self._provider.get_offers(external_id)
        )

    async def get_inventory(self, external_id: str):
        return await self._call(
            ProviderCapability.INVENTORY,
            external_id,
            lambda: self._provider.get_inventory(external_id),
        )

    async def get_history(self, external_id: str, *, days: int = 90):
        return await self._call(
            ProviderCapability.HISTORY,
            external_id,
            lambda: self._provider.get_history(external_id, days=days),
        )

    async def get_demand(self, external_id: str, *, days: int = 90):
        return await self._call(
            ProviderCapability.DEMAND,
            external_id,
            lambda: self._provider.get_demand(external_id, days=days),
        )

    async def get_competition(self, external_id: str, *, days: int = 30):
        return await self._call(
            ProviderCapability.COMPETITION,
            external_id,
            lambda: self._provider.get_competition(external_id, days=days),
        )

    async def health_check(self) -> bool:
        try:
            return await self._provider.health_check()
        except Exception:  # noqa: BLE001 - a health check must not raise
            return False

    async def close(self) -> None:
        await self._provider.close()
