import asyncio
from datetime import timedelta

import pytest

from apps.core.runtime.periodic import Action, JobRunner, PeriodicJob
from apps.core.stubs import SleepStub


async def _run_inline(action: Action) -> object:
    return action()


def _explode() -> object:
    raise RuntimeError('boom')


@pytest.fixture
def f_calls() -> list[str]:
    return []


@pytest.fixture
def f_timeline() -> list[object]:
    return []


@pytest.fixture
def f_runner(f_timeline: list[object]) -> JobRunner:
    return JobRunner(run_in_thread=_run_inline, sleep=SleepStub(timeline=f_timeline, wakeups=1))


@pytest.fixture
def f_job(f_calls: list[str]) -> PeriodicJob:
    return PeriodicJob(name='record', interval=timedelta(seconds=1), action=lambda: f_calls.append('ran'))


@pytest.fixture
def f_failing_job() -> PeriodicJob:
    return PeriodicJob(name='explode', interval=timedelta(seconds=1), action=_explode)


@pytest.fixture
def f_delayed_job(f_timeline: list[object]) -> PeriodicJob:
    return PeriodicJob(
        name='check',
        interval=timedelta(seconds=30),
        action=lambda: f_timeline.append('ran'),
        delay=timedelta(minutes=1),
    )


def test_run_once_executes_action(f_runner: JobRunner, f_job: PeriodicJob, f_calls: list[str]) -> None:
    asyncio.run(f_runner.run_once(f_job))

    assert f_calls == ['ran']


def test_run_once_survives_failing_action(f_runner: JobRunner, f_failing_job: PeriodicJob) -> None:
    assert asyncio.run(f_runner.run_once(f_failing_job)) is None


def test_run_forever_waits_the_delay_before_the_first_run(
    f_runner: JobRunner,
    f_delayed_job: PeriodicJob,
    f_timeline: list[object],
) -> None:
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(f_runner.run_forever(f_delayed_job))

    assert f_timeline == [60.0, 'ran', 30.0]


def test_run_forever_runs_a_job_without_delay_at_once(
    f_runner: JobRunner,
    f_job: PeriodicJob,
    f_calls: list[str],
    f_timeline: list[object],
) -> None:
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(f_runner.run_forever(f_job))

    assert (f_timeline, f_calls) == ([0.0, 1.0], ['ran'])
