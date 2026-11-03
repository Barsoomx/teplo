import asyncio
from collections.abc import Awaitable, Callable, Sequence

from apps.core.runtime.periodic import JobRunner, PeriodicJob
from apps.core.runtime.server import serve_asgi
from apps.system.services.jobs import shutdown_jobs, startup_jobs, teplo_jobs

Server = Callable[[str, int], Awaitable[None]]


class TeploRuntime:
    def __init__(
        self,
        runner: JobRunner,
        serve: Server,
        startup: Sequence[PeriodicJob],
        jobs: Sequence[PeriodicJob],
        shutdown: Sequence[PeriodicJob],
    ) -> None:
        self._runner = runner
        self._serve = serve
        self._startup = startup
        self._jobs = jobs
        self._shutdown = shutdown

    @classmethod
    def init(cls) -> 'TeploRuntime':
        return cls(
            runner=JobRunner.init(),
            serve=serve_asgi,
            startup=startup_jobs(),
            jobs=teplo_jobs(),
            shutdown=shutdown_jobs(),
        )

    async def run(self, host: str, port: int) -> None:
        for job in self._startup:
            await self._runner.run_once(job)
        tasks = [asyncio.create_task(self._runner.run_forever(job)) for job in self._jobs]
        try:
            await self._serve(host, port)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for job in self._shutdown:
                await self._runner.run_once(job)
