import asyncio
from datetime import timedelta

import pytest

from apps.core.runtime.periodic import Action, JobRunner, PeriodicJob


async def _run_inline(action: Action) -> object:
    return action()


def _explode() -> object:
    raise RuntimeError('boom')


@pytest.fixture
def f_calls() -> list[str]:
    return []


@pytest.fixture
def f_runner() -> JobRunner:
    return JobRunner(run_in_thread=_run_inline)


@pytest.fixture
def f_job(f_calls: list[str]) -> PeriodicJob:
    return PeriodicJob(name='record', interval=timedelta(seconds=1), action=lambda: f_calls.append('ran'))


@pytest.fixture
def f_failing_job() -> PeriodicJob:
    return PeriodicJob(name='explode', interval=timedelta(seconds=1), action=_explode)


def test_run_once_executes_action(f_runner: JobRunner, f_job: PeriodicJob, f_calls: list[str]) -> None:
    asyncio.run(f_runner.run_once(f_job))

    assert f_calls == ['ran']


def test_run_once_survives_failing_action(f_runner: JobRunner, f_failing_job: PeriodicJob) -> None:
    assert asyncio.run(f_runner.run_once(f_failing_job)) is None
