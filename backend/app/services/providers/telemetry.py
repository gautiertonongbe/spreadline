"""Provider call telemetry buffer.

Provider calls are recorded for cost control and reliability analysis, but the
write must never happen inside the request that made the call. Opening a second
database session mid-request to insert a log row puts a concurrent writer against
the transaction still in flight, which on SQLite blocks until the lock times out
and on any database is an unnecessary round trip on the hot path.

So calls land in a bounded in-memory buffer and are drained afterwards, once the
request's own session has been closed.

The buffer is intentionally lossy under extreme load: a bound of
``MAX_BUFFERED`` records means telemetry can never grow without limit, and
dropping observability data is strictly better than exhausting memory.
"""

from __future__ import annotations

import threading
from collections import deque

from app.core.logging import get_logger
from app.services.providers.reliability import CallRecord

logger = get_logger(__name__)

MAX_BUFFERED = 2000

_buffer: deque[CallRecord] = deque(maxlen=MAX_BUFFERED)
_lock = threading.Lock()
_dropped = 0


def record(call: CallRecord) -> None:
    global _dropped
    with _lock:
        if len(_buffer) == MAX_BUFFERED:
            _dropped += 1
        _buffer.append(call)


def drain() -> list[CallRecord]:
    with _lock:
        records = list(_buffer)
        _buffer.clear()
        return records


def dropped_count() -> int:
    return _dropped


def flush_to_database() -> int:
    """Persist buffered records. Safe to call from anywhere, never raises."""
    records = drain()
    if not records:
        return 0
    try:
        from app.core.database import session_scope
        from app.models.observations import ProviderRequest

        with session_scope() as session:
            session.add_all(
                ProviderRequest(
                    provider=item.provider,
                    capability=item.capability,
                    target=item.target,
                    succeeded=item.succeeded,
                    latency_ms=item.latency_ms,
                    attempts=item.attempts,
                    error_code=item.error_code,
                    error_message=item.error_message,
                )
                for item in records
            )
        return len(records)
    except Exception:  # noqa: BLE001 - observability must not break the caller
        logger.debug("could not persist provider telemetry", exc_info=True)
        return 0
