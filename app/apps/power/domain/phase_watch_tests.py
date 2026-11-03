from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.loads import LoadPhase
from apps.power.domain.phase_watch import (
    OverloadWatch,
    PhaseAmps,
    PhaseWatch,
    PhaseWatchStep,
    calm_phases,
    calming_phases,
    overloaded_phases,
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
OVERLOAD = OverloadWatch(above_since=100.0, overloaded=True)
OVERLOADED = PhaseWatch(live=OVERLOAD, dry_run=OVERLOAD)
ALARMED = PhaseWatch(measured_above_since=100.0, alarmed=True, alarm_since=100.0, alarm_amps=Decimal('31.0'))


def _amps(measured: Decimal, read_at: float, live_relief: Decimal, dry_run_relief: Decimal) -> PhaseAmps:
    return PhaseAmps(
        measured=measured,
        live=measured - live_relief,
        dry_run=measured - live_relief - dry_run_relief,
        read_at=read_at,
    )


def _watched(
    watch: PhaseWatch,
    readings: Sequence[tuple[float, Decimal | None]],
    live_relief: Decimal = NO_RELIEF,
    dry_run_relief: Decimal = NO_RELIEF,
    read_at: float | None = None,
) -> list[PhaseWatchStep]:
    steps: list[PhaseWatchStep] = []
    for at, measured in readings:
        taken_at = at if read_at is None else read_at
        amps = None if measured is None else _amps(measured, taken_at, live_relief, dry_run_relief)
        steps.append(watch_phase(watch, amps, at, THRESHOLDS))
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


def test_a_reading_watched_again_advances_no_hold() -> None:
    steps = _watched(PhaseWatch(), [(100.0 + second, Decimal('25.5')) for second in range(4)], read_at=100.0)

    assert ([step.overloaded for step in steps], steps[-1].watch) == (
        [False] * 4,
        PhaseWatch(
            live=OverloadWatch(above_since=100.0),
            dry_run=OverloadWatch(above_since=100.0),
            measured_above_since=100.0,
            read_at=100.0,
        ),
    )


def test_a_reading_at_the_shed_threshold_restarts_the_shed_hold() -> None:
    currents = [Decimal('25.0'), Decimal('25.0'), Decimal('24.0'), Decimal('25.0'), Decimal('25.0'), Decimal('25.0')]

    steps = _watched(PhaseWatch(), [(100.0 + second, current) for second, current in enumerate(currents)])

    assert ([step.overloaded for step in steps], steps[-1].watch.live.above_since) == ([False] * 6, 103.0)


def test_relief_of_live_sheds_keeps_both_views_from_overloading() -> None:
    steps = _watched(
        PhaseWatch(),
        [(100.0 + second, Decimal('27.0')) for second in range(5)],
        live_relief=Decimal('6.0'),
    )

    assert (
        [step.overloaded for step in steps],
        steps[-1].watch.live.overloaded,
        steps[-1].watch.dry_run.overloaded,
    ) == (
        [False] * 5,
        False,
        False,
    )


def test_relief_of_dry_run_sheds_keeps_only_the_dry_run_view_from_overloading() -> None:
    steps = _watched(
        PhaseWatch(),
        [(100.0 + second, Decimal('27.0')) for second in range(4)],
        dry_run_relief=Decimal('6.0'),
    )

    assert (
        [step.overloaded for step in steps],
        steps[-1].watch.live.overloaded,
        steps[-1].watch.dry_run.overloaded,
    ) == (
        [False, False, False, True],
        True,
        False,
    )


def test_overload_ends_after_the_restore_hold_at_or_below_the_restore_threshold(
    f_overloaded_watch: PhaseWatch,
) -> None:
    steps = _watched(f_overloaded_watch, [(200.0, Decimal('18.0')), (319.0, Decimal('17.2')), (320.0, Decimal('18.0'))])

    assert ([step.calmed for step in steps], steps[-1].watch.live.overloaded) == ([False, False, True], False)


def test_overload_lasts_while_the_phase_stays_above_the_restore_threshold(f_overloaded_watch: PhaseWatch) -> None:
    steps = _watched(f_overloaded_watch, [(200.0, Decimal('19.0')), (800.0, Decimal('18.1'))])

    assert ([step.calmed for step in steps], steps[-1].watch.live.overloaded) == ([False, False], True)


def test_dry_run_sheds_calm_only_the_dry_run_view_while_the_measured_phase_stays_overloaded(
    f_overloaded_watch: PhaseWatch,
) -> None:
    steps = _watched(
        f_overloaded_watch,
        [(200.0, Decimal('26.0')), (320.0, Decimal('26.0'))],
        dry_run_relief=Decimal('14.0'),
    )

    assert (
        [step.calmed for step in steps],
        steps[-1].watch.live.overloaded,
        steps[-1].watch.dry_run.overloaded,
        calm_phases({LoadPhase.L2: steps[-1].watch}, 320.0, THRESHOLDS, live=False),
        calm_phases({LoadPhase.L2: steps[-1].watch}, 320.0, THRESHOLDS, live=True),
    ) == ([False, False], True, False, frozenset({LoadPhase.L2}), frozenset())


def test_short_silence_pauses_the_calm_holds_and_keeps_the_overload(f_overloaded_watch: PhaseWatch) -> None:
    steps = _watched(
        f_overloaded_watch,
        [
            (200.0, Decimal('17.0')),
            (250.0, None),
            (270.0, Decimal('17.0')),
            (339.0, Decimal('17.0')),
            (340.0, Decimal('17.0')),
        ],
    )
    calm = OverloadWatch(calm_since=220.0)

    assert (
        [step.calmed for step in steps],
        steps[1].watch.live,
        steps[1].watch.silent_since,
        steps[-1].watch,
    ) == (
        [False, False, False, False, True],
        replace(OVERLOAD, above_since=None, calm_since=200.0),
        250.0,
        replace(f_overloaded_watch, live=calm, dry_run=calm, measured_calm_since=220.0, read_at=340.0),
    )


def test_silence_as_long_as_the_meter_lost_hold_restarts_the_calm_holds(f_overloaded_watch: PhaseWatch) -> None:
    steps = _watched(f_overloaded_watch, [(200.0, Decimal('17.0')), (250.0, None), (310.0, Decimal('17.0'))])
    calm = replace(OVERLOAD, above_since=None, calm_since=310.0)

    assert ([step.calmed for step in steps], steps[-1].watch) == (
        [False, False, False],
        replace(f_overloaded_watch, live=calm, dry_run=calm, measured_calm_since=310.0, read_at=310.0),
    )


def test_silence_restarts_the_shed_hold() -> None:
    steps = _watched(
        PhaseWatch(),
        [
            (100.0, Decimal('25.0')),
            (101.0, Decimal('25.0')),
            (102.0, None),
            (110.0, Decimal('25.0')),
            (111.0, Decimal('25.0')),
        ],
    )

    assert ([step.overloaded for step in steps], steps[-1].watch.live.above_since) == ([False] * 5, 110.0)


def test_alarm_rises_after_a_minute_above_the_shed_threshold_whatever_the_relief() -> None:
    steps = _watched(PhaseWatch(), [(100.0, Decimal('30.0')), (160.0, Decimal('31.0'))], live_relief=Decimal('12.0'))

    assert ([step.alarmed for step in steps], steps[-1].watch) == (
        [False, True],
        PhaseWatch(
            measured_above_since=100.0,
            alarmed=True,
            alarm_since=100.0,
            alarm_amps=Decimal('31.0'),
            read_at=160.0,
        ),
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
    ('watch', 'current', 'expected'),
    [
        (OVERLOAD, Decimal('20.1'), True),
        (OVERLOAD, Decimal('20.0'), False),
        (replace(OVERLOAD, last_shed_at=299.5), Decimal('26.0'), False),
        (replace(OVERLOAD, last_shed_at=299.0), Decimal('26.0'), True),
        (OverloadWatch(), Decimal('26.0'), False),
        (OVERLOAD, None, False),
    ],
    ids=[
        'overloaded_above_the_start_ceiling',
        'at_the_start_ceiling',
        'within_a_second_of_the_last_shed',
        'a_second_after_the_last_shed',
        'not_overloaded',
        'stale_meter',
    ],
)
def test_a_shed_is_due_while_an_overloaded_view_stays_above_the_start_ceiling(
    watch: OverloadWatch,
    current: Decimal | None,
    expected: bool,
) -> None:
    assert shed_due(watch, current, 300.0, THRESHOLDS) is expected


@pytest.mark.parametrize(
    ('live', 'expected'),
    [
        (True, replace(OVERLOADED, live=replace(OVERLOAD, last_shed_at=300.0))),
        (False, replace(OVERLOADED, dry_run=replace(OVERLOAD, last_shed_at=300.0))),
    ],
    ids=['live', 'dry_run'],
)
def test_with_shed_remembers_the_moment_of_the_shed_in_its_view(
    f_overloaded_watch: PhaseWatch,
    live: bool,
    expected: PhaseWatch,
) -> None:
    assert with_shed(f_overloaded_watch, 300.0, live=live) == expected


def test_calm_phases_are_those_calm_for_the_restore_hold_in_the_view() -> None:
    watches = {
        LoadPhase.L1: PhaseWatch(live=OverloadWatch(calm_since=100.0), dry_run=OverloadWatch(calm_since=100.0)),
        LoadPhase.L2: PhaseWatch(live=OverloadWatch(calm_since=150.0), dry_run=OverloadWatch(calm_since=100.0)),
        LoadPhase.L3: PhaseWatch(),
    }

    assert (
        calm_phases(watches, 220.0, THRESHOLDS, live=True),
        calm_phases(watches, 220.0, THRESHOLDS, live=False),
    ) == (
        frozenset({LoadPhase.L1}),
        frozenset({LoadPhase.L1, LoadPhase.L2}),
    )


def test_calm_phases_leave_out_a_phase_gone_silent() -> None:
    watches = {LoadPhase.L1: PhaseWatch(live=OverloadWatch(calm_since=100.0), silent_since=200.0)}

    assert calm_phases(watches, 300.0, THRESHOLDS, live=True) == frozenset()


def test_calming_phases_are_those_at_or_below_the_restore_threshold_in_the_view_silent_or_not() -> None:
    watches = {
        LoadPhase.L1: PhaseWatch(live=OverloadWatch(calm_since=100.0)),
        LoadPhase.L2: PhaseWatch(dry_run=OverloadWatch(calm_since=150.0), silent_since=200.0),
        LoadPhase.L3: PhaseWatch(),
    }

    assert (
        calming_phases(watches, live=True),
        calming_phases(watches, live=False),
    ) == (
        frozenset({LoadPhase.L1}),
        frozenset({LoadPhase.L2}),
    )


def test_overloaded_phases_are_those_overloaded_in_the_view() -> None:
    watches = {
        LoadPhase.L1: OVERLOADED,
        LoadPhase.L2: PhaseWatch(live=OVERLOAD),
        LoadPhase.L3: PhaseWatch(),
    }

    assert (
        overloaded_phases(watches, live=True),
        overloaded_phases(watches, live=False),
    ) == (
        frozenset({LoadPhase.L1, LoadPhase.L2}),
        frozenset({LoadPhase.L1}),
    )
