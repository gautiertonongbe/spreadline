"""Time handling.

Every timestamp in Spreadline is timezone-aware UTC. Backtesting depends on
being able to ask "what did we know at time T", so wall-clock reads go through
``utcnow()`` and never through ``datetime.now()`` scattered across domains.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Attach UTC to a naive datetime; convert an aware one.

    SQLite round-trips DateTime columns without the tzinfo, so rows read back
    from a local test database arrive naive. Treating them as UTC is correct
    because that is the only thing we ever write.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso_utc(value: datetime | None) -> str | None:
    """Serialise a timestamp with an explicit UTC offset, or ``None``.

    Anything hand-built into a payload dict goes through here rather than
    calling ``isoformat()`` directly. A naive ``isoformat()`` emits
    ``2026-09-12T21:05:45`` with nothing saying which zone that is, and every
    JavaScript client then parses it as local time: west of UTC the value lands
    hours in the future and a timestamp four minutes old renders as a time
    still to come.
    """
    if value is None:
        return None
    return ensure_utc(value).isoformat()


def age_seconds(value: datetime, *, now: datetime | None = None) -> float:
    return ((now or utcnow()) - ensure_utc(value)).total_seconds()


def days_ago(days: int, *, now: datetime | None = None) -> datetime:
    return (now or utcnow()) - timedelta(days=days)


def is_fresh(value: datetime, ttl_seconds: int, *, now: datetime | None = None) -> bool:
    return age_seconds(value, now=now) <= ttl_seconds
