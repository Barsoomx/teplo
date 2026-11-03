from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.notifications.domain.alerts import PhaseAlarm
from apps.notifications.services.phase_overload_source import PhaseOverloadSource
from apps.power.domain.control import LoadControl, PhaseThresholds
from apps.power.domain.guard import GuardLoads, GuardState
from apps.power.domain.loads import LoadPhase
from apps.power.domain.phase_watch import PhaseWatch
from apps.power.services.guard_book import GuardBook

STARTED_AT = 1000.0
LOADS = GuardLoads(
    records=(),
    control=LoadControl(
        live=False,
        thresholds=PhaseThresholds(
            start_ceiling=Decimal('20.0'),
            shed=Decimal('24.0'),
            shed_hold=timedelta(seconds=3),
            restore=Decimal('18.0'),
            restore_hold=timedelta(minutes=2),
            shed_interval=timedelta(seconds=1),
            restore_interval=timedelta(minutes=1),
            start_interval=timedelta(seconds=30),
            meter_lost_after=timedelta(seconds=60),
        ),
        default_amps=Decimal('6.0'),
    ),
    live_zones=frozenset(),
    meter='phase-meter',
    read_at=STARTED_AT,
)
ALARMED = PhaseWatch(
    measured_above_since=STARTED_AT + 30,
    alarmed=True,
    alarm_since=STARTED_AT + 30,
    alarm_amps=Decimal('31.27'),
)


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=STARTED_AT + 90)


@pytest.fixture
def f_source_of_an_alarmed_l2(clock: MonotonicClockStub) -> PhaseOverloadSource:
    book = GuardBook()
    state = GuardState.started(LOADS, STARTED_AT)
    book.remember(
        replace(state, phases=replace(state.phases, watches={**state.phases.watches, LoadPhase.L2: ALARMED})),
    )
    return PhaseOverloadSource(book=book, clock=clock)


@pytest.fixture
def f_source_before_the_guard_runs(clock: MonotonicClockStub) -> PhaseOverloadSource:
    return PhaseOverloadSource(book=GuardBook(), clock=clock)


def test_source_names_every_phase_with_the_alarm_the_guard_holds(
    f_source_of_an_alarmed_l2: PhaseOverloadSource,
) -> None:
    assert f_source_of_an_alarmed_l2.phases() == [
        PhaseAlarm(phase='l1', name='L1', alarmed=False, amps=None, began_ago=None),
        PhaseAlarm(phase='l2', name='L2', alarmed=True, amps=Decimal('31.27'), began_ago=timedelta(minutes=1)),
        PhaseAlarm(phase='l3', name='L3', alarmed=False, amps=None, began_ago=None),
    ]


def test_source_has_no_phase_before_the_guard_runs(f_source_before_the_guard_runs: PhaseOverloadSource) -> None:
    assert f_source_before_the_guard_runs.phases() == []
