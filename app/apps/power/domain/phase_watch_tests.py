from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.loads import LoadPhase
from apps.power.domain.phase_watch import (
    PhaseWatch,
    PhaseWatchStep,
    calm_phases,
    shed_due,
    watch_phase,
    with_shed,
)

THRESHOLDS = PhaseThresholds(
    start_ceiling=Decimal('20.0'),
    shed=Decimal('24.0'),
    shed_hold=timedelta(seconds=3),
    restore=Decimal('18.0'),
    restore_hold=timedelta(minutes=2),
    shed_interval=timedelta(seconds=1),
    restore_interval=timedelta(minutes=1),
    start_interval=timedelta(seconds=30),
    meter_lost_after=timedelta(seconds=60),
)
NO_RELIEF = Decimal(0)
OVERLOADED = PhaseWatch(above_since=100.0, overloaded=True)
ALARMED = PhaseWatch(measured_above_since=100.0, alarmed=True, alarm_since=100.0, alarm_amps=Decimal('31.0'))


def _watched(
    watch: PhaseWatch,
    readings: Sequence[tuple[float, Decimal | None]],
    relief: Decimal = NO_RELIEF,
) -> list[PhaseWatchStep]:
    steps: list[PhaseWatchStep] = []
    for at, measured in readings:
        steps.append(watch_phase(watch, measured, relief, at, THRESHOLDS))
        watch = steps[-1].watch
    return steps


@pytest.fixture
def f_overloaded_watch() -> PhaseWatch:
    return OVERLOADED


@pytest.fixture
def f_alarmed_watch() -> PhaseWatch:
    return ALARMED


def test_phase_overloads_once_above_the_shed_threshold_for_the_shed_hold() -> None:
    steps = _watched(PhaseWatch(), [(100.0 + second, Decimal('24.1')) for second in range(4)])

    assert [step.overloaded for step in steps] == [False, False, False, True]


def test_a_reading_at_the_shed_threshold_restarts_the_shed_hold() -> None:
    currents = [Decimal('25.0'), Decimal('25.0'), Decimal('24.0'), Decimal('25.0'), Decimal('25.0'), Decimal('25.0')]

    steps = _watched(PhaseWatch(), [(100.0 + second, current) for second, current in enumerate(currents)])

    assert ([step.overloaded for step in steps], steps[-1].watch.above_since) == ([False] * 6, 103.0)


def test_relief_of_sheds_keeps_a_phase_from_overloading() -> None:
    steps = _watched(PhaseWatch(), [(100.0 + second, Decimal('27.0')) for second in range(5)], relief=Decimal('6.0'))

    assert ([step.overloaded for step in steps], steps[-1].watch.overloaded) == ([False] * 5, False)


def test_overload_ends_after_the_restore_hold_at_or_below_the_restore_threshold(
    f_overloaded_watch: PhaseWatch,
) -> None:
    steps = _watched(f_overloaded_watch, [(200.0, Decimal('18.0')), (319.0, Decimal('17.2')), (320.0, Decimal('18.0'))])

    assert ([step.calmed for step in steps], steps[-1].watch.overloaded) == ([False, False, True], False)


def test_overload_lasts_while_the_phase_stays_above_the_restore_threshold(f_overloaded_watch: PhaseWatch) -> None:
    steps = _watched(f_overloaded_watch, [(200.0, Decimal('19.0')), (800.0, Decimal('18.1'))])

    assert ([step.calmed for step in steps], steps[-1].watch.overloaded) == ([False, False], True)


def test_stale_meter_restarts_the_holds_and_keeps_the_overload(f_overloaded_watch: PhaseWatch) -> None:
    steps = _watched(
        f_overloaded_watch,
        [(200.0, Decimal('17.0')), (300.0, None), (330.0, Decimal('17.0')), (449.0, Decimal('17.0'))],
    )

    assert ([step.calmed for step in steps], steps[1].watch.calm_since, steps[-1].watch) == (
        [False, False, False, False],
        None,
        replace(f_overloaded_watch, above_since=None, calm_since=330.0, measured_calm_since=330.0),
    )


def test_alarm_rises_after_a_minute_above_the_shed_threshold_whatever_the_relief() -> None:
    steps = _watched(PhaseWatch(), [(100.0, Decimal('30.0')), (160.0, Decimal('31.0'))], relief=Decimal('12.0'))

    assert ([step.alarmed for step in steps], steps[-1].watch) == (
        [False, True],
        PhaseWatch(measured_above_since=100.0, alarmed=True, alarm_since=100.0, alarm_amps=Decimal('31.0')),
    )


def test_alarm_is_raised_once_an_episode(f_alarmed_watch: PhaseWatch) -> None:
    steps = _watched(f_alarmed_watch, [(200.0, Decimal('19.0')), (300.0, Decimal('33.0'))])

    assert ([step.alarmed for step in steps], steps[-1].watch.alarm_amps) == ([False, False], Decimal('31.0'))


def test_alarm_clears_after_the_restore_hold_at_or_below_the_restore_threshold(f_alarmed_watch: PhaseWatch) -> None:
    steps = _watched(f_alarmed_watch, [(200.0, Decimal('18.0')), (320.0, Decimal('16.0'))])

    assert ([step.alarm_cleared for step in steps], steps[-1].watch.alarm_since, steps[-1].watch.alarm_amps) == (
        [False, True],
        None,
        None,
    )


@pytest.mark.parametrize(
    ('watch', 'measured', 'relief', 'expected'),
    [
        (OVERLOADED, Decimal('20.1'), NO_RELIEF, True),
        (OVERLOADED, Decimal('26.0'), Decimal('6.0'), False),
        (replace(OVERLOADED, last_shed_at=299.5), Decimal('26.0'), NO_RELIEF, False),
        (replace(OVERLOADED, last_shed_at=299.0), Decimal('26.0'), NO_RELIEF, True),
        (PhaseWatch(), Decimal('26.0'), NO_RELIEF, False),
        (OVERLOADED, None, NO_RELIEF, False),
    ],
    ids=[
        'overloaded_above_the_start_ceiling',
        'relief_brings_it_to_the_start_ceiling',
        'within_a_second_of_the_last_shed',
        'a_second_after_the_last_shed',
        'not_overloaded',
        'stale_meter',
    ],
)
def test_a_shed_is_due_while_an_overloaded_phase_less_its_relief_stays_above_the_start_ceiling(
    watch: PhaseWatch,
    measured: Decimal | None,
    relief: Decimal,
    expected: bool,
) -> None:
    assert shed_due(watch, measured, relief, 300.0, THRESHOLDS) is expected


def test_with_shed_remembers_the_moment_of_the_shed(f_overloaded_watch: PhaseWatch) -> None:
    assert with_shed(f_overloaded_watch, 300.0) == replace(f_overloaded_watch, last_shed_at=300.0)


def test_calm_phases_are_those_calm_for_the_restore_hold() -> None:
    watches = {
        LoadPhase.L1: PhaseWatch(calm_since=100.0),
        LoadPhase.L2: PhaseWatch(calm_since=150.0),
        LoadPhase.L3: PhaseWatch(),
    }

    assert calm_phases(watches, 220.0, THRESHOLDS) == frozenset({LoadPhase.L1})
