from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadKind, LoadPhase
from apps.power.domain.meter_watch import (
    MeterCondition,
    MeterWatch,
    limp_sheds,
    meter_condition,
    phases_overlap,
    watch_meter,
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


@pytest.mark.parametrize(
    ('first', 'second', 'expected'),
    [
        (LoadPhase.L1, LoadPhase.L1, True),
        (LoadPhase.L1, LoadPhase.L2, False),
        (LoadPhase.UNKNOWN, LoadPhase.L3, True),
        (LoadPhase.L2, LoadPhase.UNKNOWN, True),
        (LoadPhase.UNKNOWN, LoadPhase.UNKNOWN, True),
    ],
    ids=['same_phase', 'other_phases', 'unknown_beside_a_phase', 'phase_beside_unknown', 'both_unknown'],
)
def test_phase_of_a_load_overlaps_its_own_and_unknown_overlaps_every_phase(
    first: LoadPhase,
    second: LoadPhase,
    expected: bool,
) -> None:
    assert phases_overlap(first, second) is expected


def test_limp_house_keeps_the_most_important_drawing_floor_of_each_phase() -> None:
    loads = [LIVING_ROOM, BOILER, KITCHEN, HALLWAY]

    assert limp_sheds(loads, EVERY_FLOOR) == (HALLWAY,)


@pytest.mark.parametrize(
    ('loads', 'expected'),
    [
        ([LIVING_ROOM, BOILER, BATHROOM_2F, KITCHEN, HALLWAY, BATHROOM_1F], (BATHROOM_1F, HALLWAY, BATHROOM_2F)),
        ([BATHROOM_2F, KITCHEN, HALLWAY], (HALLWAY, KITCHEN)),
    ],
    ids=['beside_more_important_floors_of_known_phase', 'more_important_than_floors_of_known_phase'],
)
def test_limp_house_counts_a_floor_of_unknown_phase_on_every_phase(
    loads: list[LoadStatus],
    expected: tuple[LoadStatus, ...],
) -> None:
    assert limp_sheds(loads, EVERY_FLOOR) == expected


def test_limp_house_leaves_a_floor_it_may_not_shed_drawing_and_sheds_the_less_important_floors_beside_it() -> None:
    loads = [LIVING_ROOM, BATHROOM_2F, KITCHEN]

    assert limp_sheds(loads, EVERY_FLOOR - {'bathroom-2f'}) == (KITCHEN,)


def test_limp_house_counts_only_floors_that_draw_and_are_not_shed() -> None:
    loads = [
        LIVING_ROOM,
        replace(HALLWAY, shed=True),
        replace(KITCHEN, slug='guest-room', drawing=False),
        KITCHEN,
        replace(BATHROOM_1F, phase=LoadPhase.L2, available=False, drawing=False),
    ]

    assert limp_sheds(loads, EVERY_FLOOR) == ()
