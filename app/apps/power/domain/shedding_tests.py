from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.load_status import LoadReading, LoadStatus, plug_reading
from apps.power.domain.loads import LiveShed, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.shedding import (
    SETTLED,
    Shed,
    UnconfirmedShed,
    can_hand_back,
    choose_restore,
    choose_shed,
    in_view,
    new_shed,
    relief,
    resumed_shed,
    shed_confirmed,
    switched_back_on,
    unconfirmed_shed,
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
AT = 1000.0
NOW = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)
WRITE_INTERVAL = timedelta(minutes=30)
ALL_CALM = frozenset({LoadPhase.L1, LoadPhase.L2, LoadPhase.L3})
CURRENTS = {LoadPhase.L1: Decimal('10.0'), LoadPhase.L2: Decimal('14.0'), LoadPhase.L3: Decimal('4.0')}
BOILER = LoadStatus(
    slug='boiler-1f',
    kind=LoadKind.PLUG,
    priority=1,
    phase=LoadPhase.L1,
    amps=Decimal('6.0'),
    available=True,
    drawing=True,
    shed=False,
)
HALLWAY = replace(BOILER, slug='hallway', kind=LoadKind.ZONE, priority=4, amps=Decimal('8.0'))
KITCHEN = replace(HALLWAY, slug='kitchen', priority=5, phase=LoadPhase.L2)
LIVING_ROOM = replace(HALLWAY, slug='living-room', priority=7)
BATHROOM = replace(HALLWAY, slug='bathroom-1f', priority=3, phase=LoadPhase.UNKNOWN, amps=Decimal('6.0'))
BOILER_SHED = Shed(
    load='boiler-1f',
    phase=LoadPhase.L1,
    amps=Decimal('6.0'),
    live=True,
    reason=ShedReason.OVERLOAD,
    was_on=True,
    at=AT - 65,
    settled_at=AT - 60,
)
DRY_RUN_SHED = replace(BOILER_SHED, live=False)
LIVING_ROOM_SHED = replace(BOILER_SHED, load='living-room', amps=Decimal('8.0'), was_on=None)
KITCHEN_SHED = replace(LIVING_ROOM_SHED, load='kitchen', phase=LoadPhase.L2)
BATHROOM_SHED = replace(LIVING_ROOM_SHED, load='bathroom-1f', phase=LoadPhase.UNKNOWN, reason=ShedReason.METER_LOST)
UNCONFIRMED_BOILER_SHED = UnconfirmedShed(
    shed=replace(BOILER_SHED, at=AT, settled_at=SETTLED),
    amps=Decimal('26.0'),
    at=NOW,
)
SHED_BOILER_RECORD = LoadRecord(
    slug='boiler-1f',
    name='Бойлер 1 этаж',
    kind=LoadKind.PLUG,
    zone_slug=None,
    device_slug='plug-boiler-1f',
    priority=1,
    phase=LoadPhase.UNKNOWN,
    nominal_amps=Decimal('6.0'),
    learned=None,
    live=True,
    shed=LiveShed(
        at=datetime(2026, 11, 2, 18, 40, tzinfo=UTC),
        reason=ShedReason.OVERLOAD,
        phase=LoadPhase.L2,
        was_on=True,
    ),
)


SHED_KITCHEN_RECORD = replace(
    SHED_BOILER_RECORD,
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug='thermostat-kitchen',
    priority=5,
    nominal_amps=Decimal('8.0'),
    shed=LiveShed(at=NOW, reason=ShedReason.OVERLOAD, phase=LoadPhase.L2, was_on=None),
)


def _kitchen_shed_ago(shed: timedelta, stamped: timedelta | None) -> LoadRecord:
    return replace(
        SHED_KITCHEN_RECORD,
        shed=LiveShed(at=NOW - shed, reason=ShedReason.OVERLOAD, phase=LoadPhase.L2, was_on=None),
        last_command_at=None if stamped is None else NOW - stamped,
    )


@pytest.fixture
def f_sheds() -> list[Shed]:
    return [
        replace(BOILER_SHED, settled_at=AT + 2),
        replace(LIVING_ROOM_SHED, settled_at=AT - 1),
        replace(BOILER_SHED, load='boiler-2f', amps=Decimal('4.0'), live=False, settled_at=AT - 600),
        replace(KITCHEN_SHED, amps=Decimal('5.0'), live=False),
    ]


@pytest.mark.parametrize(
    ('phase', 'expected'),
    [(LoadPhase.L1, Decimal('10.0')), (LoadPhase.L2, Decimal('5.0')), (LoadPhase.L3, Decimal(0))],
)
def test_relief_counts_live_sheds_while_they_settle_and_dry_run_sheds_until_restored(
    f_sheds: list[Shed],
    phase: LoadPhase,
    expected: Decimal,
) -> None:
    assert relief(f_sheds, phase, AT) == expected


@pytest.mark.parametrize(
    ('read_at', 'expected'),
    [(AT + 1.9, Decimal('6.0')), (AT + 2, Decimal(0))],
    ids=['reading_taken_while_it_settles', 'reading_taken_once_it_settled'],
)
def test_live_shed_relieves_its_phase_until_a_reading_taken_once_it_settled(
    f_sheds: list[Shed],
    read_at: float,
    expected: Decimal,
) -> None:
    assert relief(f_sheds[:1], LoadPhase.L1, read_at) == expected


@pytest.mark.parametrize(
    ('shed', 'live', 'expected'),
    [(BOILER_SHED, True, True), (BOILER_SHED, False, True), (DRY_RUN_SHED, True, False), (DRY_RUN_SHED, False, True)],
    ids=[
        'live_shed_in_the_live_view',
        'live_shed_in_the_dry_run_view',
        'dry_run_shed_in_the_live_view',
        'dry_run_shed_in_the_dry_run_view',
    ],
)
def test_live_view_sees_only_live_sheds_and_the_dry_run_view_every_shed(shed: Shed, live: bool, expected: bool) -> None:
    assert in_view(shed, live=live) is expected


def test_new_shed_relieves_its_phase_while_it_settles() -> None:
    assert new_shed(KITCHEN, phase=LoadPhase.L2, live=True, reason=ShedReason.OVERLOAD, was_on=None, at=AT) == Shed(
        load='kitchen',
        phase=LoadPhase.L2,
        amps=Decimal('8.0'),
        live=True,
        reason=ShedReason.OVERLOAD,
        was_on=None,
        at=AT,
        settled_at=AT + 5,
    )


def test_unconfirmed_shed_holds_a_settled_live_shed_of_a_plug_that_was_on() -> None:
    unconfirmed = unconfirmed_shed(
        BOILER,
        phase=LoadPhase.L1,
        reason=ShedReason.OVERLOAD,
        amps=Decimal('26.0'),
        at=AT,
        now=NOW,
    )

    assert unconfirmed == UNCONFIRMED_BOILER_SHED


@pytest.mark.parametrize(
    ('reading', 'expected'),
    [
        (None, None),
        (plug_reading(on=False, current=Decimal('0.0'), read_at=AT), None),
        (plug_reading(on=False, current=Decimal('0.0'), read_at=AT + 25), True),
        (plug_reading(on=True, current=Decimal('6.1'), read_at=AT + 25), False),
    ],
    ids=['plug_unavailable', 'plug_read_before_the_write_failed', 'plug_read_off_since', 'plug_read_on_since'],
)
def test_shed_whose_write_failed_is_confirmed_by_the_first_reading_since_the_failure(
    reading: LoadReading | None,
    expected: bool | None,
) -> None:
    assert shed_confirmed(UNCONFIRMED_BOILER_SHED, reading) is expected


@pytest.mark.parametrize(
    ('now', 'resumed_at'),
    [(NOW + timedelta(minutes=10), AT - 600), (NOW - timedelta(minutes=5), AT)],
    ids=['shed_ten_minutes_ago', 'shed_stored_later_than_the_clock_reads'],
)
def test_resumed_shed_carries_the_stored_shed_settled_for_every_reading_and_dated_by_its_age(
    now: datetime,
    resumed_at: float,
) -> None:
    resumed = resumed_shed(
        SHED_BOILER_RECORD,
        at=AT,
        now=now,
        default_amps=Decimal('6.0'),
        write_interval=WRITE_INTERVAL,
    )

    assert (resumed, relief([] if resumed is None else [resumed], LoadPhase.L2, AT - 1)) == (
        Shed(
            load='boiler-1f',
            phase=LoadPhase.L2,
            amps=Decimal('6.0'),
            live=True,
            reason=ShedReason.OVERLOAD,
            was_on=True,
            at=resumed_at,
            settled_at=SETTLED,
        ),
        Decimal(0),
    )


def test_resumed_zone_shed_counts_as_one_whose_write_stamp_was_lost() -> None:
    resumed = resumed_shed(
        SHED_KITCHEN_RECORD,
        at=AT,
        now=NOW,
        default_amps=Decimal('6.0'),
        write_interval=WRITE_INTERVAL,
    )

    assert resumed == Shed(
        load='kitchen',
        phase=LoadPhase.L2,
        amps=Decimal('8.0'),
        live=True,
        reason=ShedReason.OVERLOAD,
        was_on=None,
        at=AT,
        settled_at=SETTLED,
        unstamped_write_at=AT,
    )


@pytest.mark.parametrize(
    ('record', 'unstamped_write_at'),
    [
        (_kitchen_shed_ago(timedelta(minutes=20), None), AT - 1200),
        (_kitchen_shed_ago(timedelta(minutes=29), timedelta(minutes=29)), AT - 1740),
        (_kitchen_shed_ago(timedelta(minutes=30), timedelta(minutes=30)), AT),
        (_kitchen_shed_ago(timedelta(minutes=40), timedelta(minutes=10)), AT - 2400),
    ],
    ids=[
        'shed_twenty_minutes_ago',
        'shed_and_stamped_within_the_write_interval',
        'shed_and_stamped_a_write_interval_ago',
        'release_stamped_within_the_write_interval',
    ],
)
def test_resumed_zone_shed_counts_a_release_unstamped_just_before_the_restart_once_one_could_be_written(
    record: LoadRecord,
    unstamped_write_at: float,
) -> None:
    resumed = resumed_shed(record, at=AT, now=NOW, default_amps=Decimal('6.0'), write_interval=WRITE_INTERVAL)

    assert (None if resumed is None else resumed.unstamped_write_at) == unstamped_write_at


def test_resumed_shed_of_a_load_not_shed_is_none() -> None:
    resumed = resumed_shed(
        replace(SHED_BOILER_RECORD, shed=None),
        at=AT,
        now=NOW,
        default_amps=Decimal('6.0'),
        write_interval=WRITE_INTERVAL,
    )

    assert resumed is None


@pytest.mark.parametrize(
    ('shed', 'expected'),
    [
        (KITCHEN_SHED, True),
        (replace(KITCHEN_SHED, unstamped_write_at=AT - 1799), False),
        (replace(KITCHEN_SHED, unstamped_write_at=AT - 1800), True),
    ],
    ids=['every_write_stamped', 'unstamped_write_within_the_write_interval', 'unstamped_write_a_write_interval_ago'],
)
def test_zone_shed_is_handed_back_a_write_interval_after_a_write_whose_stamp_may_be_lost(
    shed: Shed,
    expected: bool,
) -> None:
    assert can_hand_back(shed, AT, WRITE_INTERVAL) is expected


@pytest.mark.parametrize(
    ('shed', 'reading', 'expected'),
    [
        (BOILER_SHED, plug_reading(on=True, current=Decimal('0.0'), read_at=AT), True),
        (BOILER_SHED, plug_reading(on=True, current=Decimal('6.1'), read_at=AT - 70), False),
        (BOILER_SHED, plug_reading(on=False, current=Decimal('0.0'), read_at=AT), False),
        (BOILER_SHED, None, False),
        (replace(BOILER_SHED, live=False), plug_reading(on=True, current=Decimal('6.1'), read_at=AT), False),
        (LIVING_ROOM_SHED, LoadReading(read_at=AT, drawing=True, closed=True, current=None), False),
    ],
    ids=[
        'plug_on_after_the_shed',
        'plug_read_before_the_shed',
        'plug_still_off',
        'plug_unavailable',
        'dry_run_shed',
        'zone',
    ],
)
def test_owner_switching_a_live_shed_plug_back_on_takes_it_over(
    shed: Shed,
    reading: LoadReading | None,
    expected: bool,
) -> None:
    assert switched_back_on(shed, reading) is expected


def test_shed_takes_the_first_drawing_load_by_priority_on_the_phase() -> None:
    assert choose_shed(LoadPhase.L1, [LIVING_ROOM, KITCHEN, BATHROOM, HALLWAY, BOILER]) == BOILER


def test_shed_turns_to_loads_of_unknown_phase_once_none_draws_on_the_phase() -> None:
    loads = [replace(BOILER, drawing=False), replace(HALLWAY, shed=True), replace(LIVING_ROOM, available=False)]

    assert choose_shed(LoadPhase.L1, [*loads, KITCHEN, BATHROOM]) == BATHROOM


def test_shed_finds_nothing_when_no_load_draws_on_the_phase() -> None:
    assert choose_shed(LoadPhase.L3, [BOILER, HALLWAY, KITCHEN]) is None


def test_restore_returns_the_most_important_shed_load_first() -> None:
    restored = choose_restore(
        [BOILER_SHED, LIVING_ROOM_SHED],
        [replace(BOILER, shed=True), replace(LIVING_ROOM, shed=True)],
        calm=ALL_CALM,
        currents=CURRENTS,
        last_restore_at=None,
        at=AT,
        thresholds=THRESHOLDS,
    )

    assert restored == LIVING_ROOM_SHED


def test_restore_passes_over_a_load_that_would_push_its_phase_over_the_start_ceiling() -> None:
    restored = choose_restore(
        [BOILER_SHED, LIVING_ROOM_SHED],
        [replace(BOILER, shed=True), replace(LIVING_ROOM, shed=True, amps=Decimal('10.5'))],
        calm=ALL_CALM,
        currents=CURRENTS,
        last_restore_at=None,
        at=AT,
        thresholds=THRESHOLDS,
    )

    assert restored == BOILER_SHED


@pytest.mark.parametrize(
    ('calm', 'last_restore_at'),
    [(ALL_CALM, AT - 59), (frozenset({LoadPhase.L2, LoadPhase.L3}), None)],
    ids=['within_a_minute_of_the_last_restore', 'phase_not_calm_for_the_restore_hold'],
)
def test_restore_waits(calm: frozenset[LoadPhase], last_restore_at: float | None) -> None:
    restored = choose_restore(
        [BOILER_SHED],
        [replace(BOILER, shed=True)],
        calm=calm,
        currents=CURRENTS,
        last_restore_at=last_restore_at,
        at=AT,
        thresholds=THRESHOLDS,
    )

    assert restored is None


@pytest.mark.parametrize(
    ('calm', 'expected'),
    [(frozenset({LoadPhase.L1, LoadPhase.L2}), None), (ALL_CALM, BATHROOM_SHED)],
    ids=['two_phases_calm', 'every_phase_calm'],
)
def test_restore_of_a_load_shed_for_no_known_phase_waits_for_every_phase(
    calm: frozenset[LoadPhase],
    expected: Shed | None,
) -> None:
    restored = choose_restore(
        [BATHROOM_SHED],
        [replace(BATHROOM, shed=True)],
        calm=calm,
        currents=CURRENTS,
        last_restore_at=None,
        at=AT,
        thresholds=THRESHOLDS,
    )

    assert restored == expected


@pytest.mark.parametrize(
    ('amps', 'expected'),
    [(Decimal('6.0'), BATHROOM_SHED), (Decimal('6.5'), None)],
    ids=['fits_every_phase', 'overruns_the_fullest_phase'],
)
def test_restore_of_a_load_of_unknown_phase_needs_room_on_every_phase(amps: Decimal, expected: Shed | None) -> None:
    restored = choose_restore(
        [BATHROOM_SHED],
        [replace(BATHROOM, shed=True, amps=amps)],
        calm=ALL_CALM,
        currents=CURRENTS,
        last_restore_at=None,
        at=AT,
        thresholds=THRESHOLDS,
    )

    assert restored == expected


def test_restore_passes_over_a_load_whose_device_is_unavailable() -> None:
    restored = choose_restore(
        [BOILER_SHED, LIVING_ROOM_SHED],
        [replace(BOILER, shed=True), replace(LIVING_ROOM, shed=True, available=False, drawing=False)],
        calm=ALL_CALM,
        currents=CURRENTS,
        last_restore_at=None,
        at=AT,
        thresholds=THRESHOLDS,
    )

    assert restored == BOILER_SHED


@pytest.mark.parametrize(
    ('boiler_amps', 'expected'),
    [(Decimal('6.0'), BOILER_SHED), (Decimal('10.5'), replace(LIVING_ROOM_SHED, restore_failed=True))],
    ids=['another_shed_has_room', 'no_other_shed_has_room'],
)
def test_restore_returns_a_shed_whose_restore_failed_only_after_every_other_ready_shed(
    boiler_amps: Decimal,
    expected: Shed,
) -> None:
    restored = choose_restore(
        [BOILER_SHED, replace(LIVING_ROOM_SHED, restore_failed=True)],
        [replace(BOILER, shed=True, amps=boiler_amps), replace(LIVING_ROOM, shed=True)],
        calm=ALL_CALM,
        currents=CURRENTS,
        last_restore_at=None,
        at=AT,
        thresholds=THRESHOLDS,
    )

    assert restored == expected


def test_restore_skips_a_shed_whose_load_is_gone() -> None:
    restored = choose_restore(
        [LIVING_ROOM_SHED],
        [replace(BOILER, shed=True)],
        calm=ALL_CALM,
        currents=CURRENTS,
        last_restore_at=None,
        at=AT,
        thresholds=THRESHOLDS,
    )

    assert restored is None
