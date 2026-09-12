"""Scheduling abstraction.

APScheduler is the implementation today (spec §4). The abstraction exists so that
moving to Celery, Temporal or anything else is a new ``Scheduler`` subclass
rather than a rewrite of every job call site.

Jobs are registered as plain callables with an interval; nothing in the domain
layer imports APScheduler.
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class JobDefinition:
    key: str
    func: Callable[..., Any]
    interval_seconds: int
    description: str = ""
    kwargs: dict[str, Any] = field(default_factory=dict)
    #: Skip the first immediate run. Refresh jobs should not all fire at boot.
    run_on_start: bool = False


class Scheduler(abc.ABC):
    """Minimal surface every backend must provide."""

    @abc.abstractmethod
    def add_job(self, job: JobDefinition) -> None: ...

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def shutdown(self) -> None: ...

    @property
    @abc.abstractmethod
    def jobs(self) -> list[str]: ...


class NoopScheduler(Scheduler):
    """Records jobs and runs nothing.

    The default in tests and in any environment where background work is handled
    elsewhere. Registering a job must never be conditional on the scheduler being
    switched on, or the job list drifts from what actually runs.
    """

    def __init__(self) -> None:
        self._jobs: list[JobDefinition] = []

    def add_job(self, job: JobDefinition) -> None:
        self._jobs.append(job)

    def start(self) -> None:
        logger.info(
            "scheduler disabled",
            extra={"context": {"registered_jobs": [job.key for job in self._jobs]}},
        )

    def shutdown(self) -> None:
        return None

    @property
    def jobs(self) -> list[str]:
        return [job.key for job in self._jobs]


class APSchedulerScheduler(Scheduler):
    def __init__(self) -> None:
        from apscheduler.schedulers.background import BackgroundScheduler

        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._definitions: list[JobDefinition] = []

    def add_job(self, job: JobDefinition) -> None:
        self._definitions.append(job)
        self._scheduler.add_job(
            job.func,
            trigger="interval",
            seconds=job.interval_seconds,
            id=job.key,
            name=job.description or job.key,
            kwargs=job.kwargs,
            # A job that overran must not stack up a queue of missed runs.
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )

    def start(self) -> None:
        self._scheduler.start()
        logger.info(
            "scheduler started",
            extra={"context": {"jobs": [job.key for job in self._definitions]}},
        )

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    @property
    def jobs(self) -> list[str]:
        return [job.key for job in self._definitions]


def build_scheduler() -> Scheduler:
    from app.core.config import settings

    if not settings.scheduler_enabled or settings.scheduler_backend == "noop":
        return NoopScheduler()
    return APSchedulerScheduler()
