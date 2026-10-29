import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

import structlog
from django.db import close_old_connections

logger = structlog.get_logger(__name__)

Action = Callable[[], object]


@dataclass(frozen=True, kw_only=True)
class PeriodicJob:
    name: str
    interval: timedelta
    action: Action


class JobRunner:
    def __init__(self, run_in_thread: Callable[[Action], Awaitable[object]]) -> None:
        self._run_in_thread = run_in_thread

    @classmethod
    def init(cls) -> 'JobRunner':
        return cls(run_in_thread=asyncio.to_thread)

    async def run_forever(self, job: PeriodicJob) -> None:
        while True:
            await self.run_once(job)
            await asyncio.sleep(job.interval.total_seconds())

    async def run_once(self, job: PeriodicJob) -> None:
        try:
            await self._run_in_thread(_with_fresh_connections(job.action))
        except Exception:
            logger.exception('periodic_job_failed', job=job.name)


def _with_fresh_connections(action: Action) -> Action:
    def run() -> object:
        close_old_connections()
        try:
            return action()
        finally:
            close_old_connections()

    return run
