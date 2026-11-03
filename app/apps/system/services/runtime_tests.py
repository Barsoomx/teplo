import asyncio
from datetime import timedelta
from functools import partial

import pytest

from apps.core.runtime.periodic import JobRunner, PeriodicJob
from apps.core.stubs import IdleSleepStub, ServerStub, YieldingThreadStub
from apps.system.services.jobs import ONCE
from apps.system.services.runtime import TeploRuntime

STARTUP = ('journal_startup', 'recover_write_holds', 'start_mqtt_listener', 'start_command_listener')
JOBS = ('guard_phases', 'run_heating_cycle')
SHUTDOWN = ('flush_samples', 'stop_mqtt_listener', 'stop_command_listener')


def _jobs(timeline: list[str], names: tuple[str, ...], interval: timedelta) -> tuple[PeriodicJob, ...]:
    return tuple(PeriodicJob(name=name, interval=interval, action=partial(timeline.append, name)) for name in names)


@pytest.fixture
def f_timeline() -> list[str]:
    return []


@pytest.fixture
def f_runtime(f_timeline: list[str]) -> TeploRuntime:
    return TeploRuntime(
        runner=JobRunner(run_in_thread=YieldingThreadStub(), sleep=IdleSleepStub()),
        serve=ServerStub(timeline=f_timeline, turns=5),
        startup=_jobs(f_timeline, STARTUP, ONCE),
        jobs=_jobs(f_timeline, JOBS, timedelta(seconds=1)),
        shutdown=_jobs(f_timeline, SHUTDOWN, ONCE),
    )


def test_startup_steps_run_before_any_job_or_the_panel_and_the_shutdown_steps_after_them(
    f_runtime: TeploRuntime,
    f_timeline: list[str],
) -> None:
    asyncio.run(f_runtime.run(host='127.0.0.1', port=8088))

    assert f_timeline == [*STARTUP, 'serve 127.0.0.1:8088', *JOBS, *SHUTDOWN]
