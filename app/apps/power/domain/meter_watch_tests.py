from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadKind, LoadPhase, ShedReason
from apps.power.domain.meter_watch import (
    MeterCondition,
    MeterWatch,
    choose_limp_restore,
    limp_sheds,
    meter_condition,
    silent_phase_allows,
    watch_meter,
)
from apps.power.domain.shedding import Shed

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
STARTED_AT = 50.0
HALLWAY = LoadStatus(
    slug='hallway',
    kind=LoadKind.ZONE,
    priority=4,
    phase=LoadPhase.L1,
    amps=Decimal('8.0'),
    available=True,
    drawing=True,
    shed=False,
)
LIVING_ROOM = replace(HALLWAY, slug='living-room', priority=7)
KITCHEN = replace(HALLWAY, slug='kitchen', priority=5, phase=LoadPhase.L2)
BATHROOM_1F = replace(HALLWAY, slug='bathroom-1f', priority=3, phase=LoadPhase.UNKNOWN)
BATHROOM_2F = replace(HALLWAY, slug='bathroom-2f', priority=6, phase=LoadPhase.UNKNOWN)
BOILER = replace(HALLWAY, slug='boiler-1f', kind=LoadKind.PLUG, priority=1, amps=Decimal('6.0'))
EVERY_FLOOR = frozenset({'living-room', 'bathroom-2f', 'kitchen', 'hallway', 'bathroom-1f', 'guest-room'})
RESTORED_AT = 1000.0
KITCHEN_OFF = replace(KITCHEN, drawing=False, shed=True)
HALLWAY_OFF = replace(HALLWAY, drawing=False, shed=True)
BATHROOM_1F_OFF = replace(BATHROOM_1F, drawing=False, shed=True)
BOILER_OFF = replace(BOILER, drawing=False, shed=True)
KITCHEN_SHED = Shed(
    load='kitchen',
    phase=LoadPhase.L2,
    amps=Decimal('8.0'),
    live=True,
    reason=ShedReason.OVERLOAD,
    was_on=None,
    at=STARTED_AT,
    settled_at=STARTED_AT + 5,
)
HALLWAY_SHED = replace(KITCHEN_SHED, load='hallway', phase=LoadPhase.L1)
BATHROOM_1F_SHED = replace(KITCHEN_SHED, load='bathroom-1f', amps=Decimal('6.0'))
BOILER_SHED = replace(KITCHEN_SHED, load='boiler-1f', phase=LoadPhase.L1, amps=Decimal('6.0'), was_on=True)
LIMP_KITCHEN_SHED = replace(KITCHEN_SHED, reason=ShedReason.METER_LOST)
EVERY_PHASE = frozenset({LoadPhase.L1, LoadPhase.L2, LoadPhase.L3})


@pytest.fixture
def f_seen_meter() -> MeterWatch:
    return MeterWatch(seen=True, stale_since=None)


def test_meter_starts_unseen_and_stale_from_teplos_start() -> None:
    assert MeterWatch.started(STARTED_AT) == MeterWatch(seen=False, stale_since=STARTED_AT)


def test_fresh_meter_is_seen() -> None:
    assert watch_meter(MeterWatch.started(STARTED_AT), fresh=True, at=60.0) == MeterWatch(seen=True, stale_since=None)


def test_meter_turning_stale_keeps_the_moment_it_went_stale(f_seen_meter: MeterWatch) -> None:
    stale = watch_meter(f_seen_meter, fresh=False, at=100.0)

    assert watch_meter(stale, fresh=False, at=130.0) == MeterWatch(seen=True, stale_since=100.0)


@pytest.mark.parametrize(
    ('watch', 'expected'),
    [
        (MeterWatch(seen=True, stale_since=None), MeterCondition.FRESH),
        (MeterWatch(seen=True, stale_since=100.0), MeterCondition.STALE),
        (MeterWatch(seen=True, stale_since=99.0), MeterCondition.LOST),
        (MeterWatch.started(STARTED_AT), MeterCondition.LOST),
    ],
    ids=['fresh', 'stale_for_less_than_a_minute', 'stale_for_a_minute', 'silent_since_the_start'],
)
def test_meter_stale_for_the_meter_lost_hold_is_lost(watch: MeterWatch, expected: MeterCondition) -> None:
    assert meter_condition(watch, 159.0, THRESHOLDS) is expected


def test_limp_house_keeps_the_most_important_drawing_floor_of_each_phase() -> None:
    loads = [LIVING_ROOM, BOILER, KITCHEN, HALLWAY]

    assert limp_sheds(loads, EVERY_FLOOR) == (HALLWAY,)


@pytest.mark.parametrize(
    ('loads', 'expected'),
    [
        ([LIVING_ROOM, BOILER, BATHROOM_2F, KITCHEN, HALLWAY, BATHROOM_1F], (BATHROOM_1F, HALLWAY)),
        ([BATHROOM_2F, KITCHEN, HALLWAY], ()),
    ],
    ids=['beside_a_more_important_floor_of_unknown_phase', 'beside_floors_of_known_phases'],
)
def test_limp_house_counts_floors_of_unknown_phase_as_one_more_phase(
    loads: list[LoadStatus],
    expected: tuple[LoadStatus, ...],
) -> None:
    assert limp_sheds(loads, EVERY_FLOOR) == expected


def test_limp_house_leaves_a_floor_it_may_not_shed_drawing_and_sheds_the_less_important_floors_beside_it() -> None:
    loads = [LIVING_ROOM, replace(BATHROOM_2F, phase=LoadPhase.L1), HALLWAY]

    assert limp_sheds(loads, EVERY_FLOOR - {'bathroom-2f'}) == (HALLWAY,)


def test_limp_house_counts_only_floors_that_draw_and_are_not_shed() -> None:
    loads = [
        LIVING_ROOM,
        replace(HALLWAY, shed=True),
        replace(KITCHEN, slug='guest-room', drawing=False),
        KITCHEN,
        replace(BATHROOM_1F, phase=LoadPhase.L2, available=False, drawing=False),
    ]

    assert limp_sheds(loads, EVERY_FLOOR) == ()


def _limp_restore(
    sheds: list[Shed],
    loads: list[LoadStatus],
    last_restore_at: float | None = None,
    *,
    calm: frozenset[LoadPhase] = frozenset(),
    calming: frozenset[LoadPhase] = EVERY_PHASE,
    overloaded: frozenset[LoadPhase] = frozenset(),
    currents: dict[LoadPhase, Decimal] | None = None,
) -> Shed | None:
    return choose_limp_restore(
        sheds,
        loads,
        calm=calm,
        calming=calming,
        overloaded=overloaded,
        currents={} if currents is None else currents,
        last_restore_at=last_restore_at,
        at=RESTORED_AT,
        thresholds=THRESHOLDS,
    )


@pytest.mark.parametrize(
    ('sheds', 'loads', 'expected'),
    [
        ([KITCHEN_SHED], [KITCHEN_OFF, LIVING_ROOM], KITCHEN_SHED),
        ([KITCHEN_SHED], [KITCHEN_OFF, replace(LIVING_ROOM, phase=LoadPhase.L2)], None),
        ([KITCHEN_SHED], [KITCHEN_OFF, BATHROOM_2F], KITCHEN_SHED),
        ([BATHROOM_1F_SHED], [BATHROOM_1F_OFF, LIVING_ROOM], BATHROOM_1F_SHED),
        ([BATHROOM_1F_SHED], [BATHROOM_1F_OFF, BATHROOM_2F], None),
        ([HALLWAY_SHED, KITCHEN_SHED], [HALLWAY_OFF, KITCHEN_OFF], KITCHEN_SHED),
        ([BOILER_SHED], [BOILER_OFF], None),
        ([KITCHEN_SHED], [replace(KITCHEN_OFF, available=False)], None),
    ],
    ids=[
        'no_floor_draws_on_its_phase',
        'a_floor_draws_on_its_phase',
        'a_floor_of_unknown_phase_draws',
        'floor_of_unknown_phase_beside_a_floor_of_known_phase',
        'floor_of_unknown_phase_beside_another_floor_of_unknown_phase',
        'most_important_floor_first',
        'plug',
        'floor_whose_thermostat_is_unavailable',
    ],
)
def test_lost_meter_brings_a_shed_floor_back_once_no_other_floor_draws_on_its_phase(
    sheds: list[Shed],
    loads: list[LoadStatus],
    expected: Shed | None,
) -> None:
    assert _limp_restore(sheds, loads) == expected


@pytest.mark.parametrize(
    ('last_restore_at', 'expected'),
    [(RESTORED_AT - 59, None), (RESTORED_AT - 60, KITCHEN_SHED)],
    ids=['within_a_minute_of_the_last_restore', 'a_minute_after_the_last_restore'],
)
def test_lost_meter_brings_shed_floors_back_one_a_minute(last_restore_at: float, expected: Shed | None) -> None:
    assert _limp_restore([KITCHEN_SHED], [KITCHEN_OFF], last_restore_at) == expected


@pytest.mark.parametrize(
    ('sheds', 'overloaded', 'expected'),
    [
        ([KITCHEN_SHED], frozenset({LoadPhase.L2}), None),
        ([KITCHEN_SHED], frozenset({LoadPhase.L1}), KITCHEN_SHED),
        ([KITCHEN_SHED, HALLWAY_SHED], frozenset({LoadPhase.L2}), HALLWAY_SHED),
    ],
    ids=['its_overload_still_on', 'another_phase_overloaded', 'a_floor_shed_for_a_phase_that_calmed'],
)
def test_lost_meter_keeps_a_floor_off_while_the_phase_it_was_shed_for_went_silent_overloaded(
    sheds: list[Shed],
    overloaded: frozenset[LoadPhase],
    expected: Shed | None,
) -> None:
    assert _limp_restore(sheds, [KITCHEN_OFF, HALLWAY_OFF], overloaded=overloaded) == expected


@pytest.mark.parametrize(
    ('shed', 'calming', 'expected'),
    [
        (KITCHEN_SHED, frozenset({LoadPhase.L2}), KITCHEN_SHED),
        (KITCHEN_SHED, frozenset({LoadPhase.L1, LoadPhase.L3}), None),
        (LIMP_KITCHEN_SHED, frozenset(), LIMP_KITCHEN_SHED),
    ],
    ids=[
        'shed_for_an_overload_its_phase_read_calm',
        'shed_for_an_overload_its_phase_read_above_the_restore_threshold',
        'shed_by_limp_mode_whatever_its_phase_read',
    ],
)
def test_lost_meter_brings_a_floor_shed_for_an_overload_back_only_if_its_phase_went_silent_calm(
    shed: Shed,
    calming: frozenset[LoadPhase],
    expected: Shed | None,
) -> None:
    assert _limp_restore([shed], [KITCHEN_OFF], calming=calming) == expected


@pytest.mark.parametrize(
    ('loads', 'currents', 'calm', 'expected'),
    [
        ([KITCHEN_OFF], {LoadPhase.L2: Decimal('12.0')}, frozenset({LoadPhase.L2}), KITCHEN_SHED),
        ([KITCHEN_OFF], {LoadPhase.L2: Decimal('12.5')}, frozenset({LoadPhase.L2}), None),
        ([KITCHEN_OFF], {LoadPhase.L2: Decimal('12.0')}, frozenset({LoadPhase.L1}), None),
        (
            [KITCHEN_OFF, replace(LIVING_ROOM, phase=LoadPhase.L2)],
            {LoadPhase.L2: Decimal('12.0')},
            frozenset({LoadPhase.L2}),
            None,
        ),
        ([KITCHEN_OFF], {LoadPhase.L1: Decimal('12.0')}, frozenset({LoadPhase.L1}), KITCHEN_SHED),
    ],
    ids=[
        'calm_with_room',
        'calm_without_room',
        'not_calm',
        'calm_with_room_beside_a_floor_drawing_on_it',
        'only_another_phase_reports',
    ],
)
def test_lost_meter_brings_a_floor_back_as_a_fresh_meter_would_while_the_phase_it_was_shed_for_reports(
    loads: list[LoadStatus],
    currents: dict[LoadPhase, Decimal],
    calm: frozenset[LoadPhase],
    expected: Shed | None,
) -> None:
    assert _limp_restore([KITCHEN_SHED], loads, calm=calm, currents=currents) == expected


@pytest.mark.parametrize(
    ('shed_for', 'calming', 'overloaded', 'expected'),
    [
        (None, frozenset(), frozenset(), True),
        (None, EVERY_PHASE, frozenset({LoadPhase.L2}), False),
        (ShedReason.OVERLOAD, frozenset({LoadPhase.L2}), frozenset(), True),
        (ShedReason.OVERLOAD, frozenset({LoadPhase.L1, LoadPhase.L3}), frozenset(), False),
        (ShedReason.METER_LOST, frozenset(), frozenset(), True),
    ],
    ids=[
        'not_overloaded',
        'overloaded_when_it_went_silent',
        'shed_for_an_overload_on_it_and_calm_when_it_went_silent',
        'shed_for_an_overload_on_it_and_above_the_restore_threshold_when_it_went_silent',
        'shed_by_limp_mode',
    ],
)
def test_silent_phase_allows_a_floor_unless_it_went_silent_overloaded_or_not_calm_after_an_overload(
    shed_for: ShedReason | None,
    calming: frozenset[LoadPhase],
    overloaded: frozenset[LoadPhase],
    expected: bool,
) -> None:
    assert silent_phase_allows(LoadPhase.L2, shed_for=shed_for, calming=calming, overloaded=overloaded) is expected
