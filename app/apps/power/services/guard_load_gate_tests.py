from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.drive import StartAnswer, ZoneStart
from apps.core.stubs import MonotonicClockStub
from apps.power.domain.control import LoadControl, PhaseThresholds
from apps.power.domain.events import StartWouldWaitDryRun
from apps.power.domain.guard import GuardLoads, GuardState, SeenReading, ShedState
from apps.power.domain.load_status import thermostat_reading
from apps.power.domain.loads import LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.meter_watch import MeterWatch
from apps.power.domain.shedding import Shed
from apps.power.domain.start_gate import StartRefusal
from apps.power.services.guard_book import GuardBook
from apps.power.services.guard_load_gate import GuardLoadGate

AT = 1000.0
NOW = datetime(2026, 11, 2, 3, 30, tzinfo=UTC)
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
KITCHEN = LoadRecord(
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug='thermostat-kitchen',
    priority=5,
    phase=LoadPhase.L2,
    nominal_amps=Decimal('8.0'),
    learned=None,
    live=True,
    shed=None,
)
LIVING_ROOM = replace(
    KITCHEN,
    slug='living-room',
    name='Гостиная',
    zone_slug='living-room',
    device_slug='thermostat-living-room',
    priority=7,
)
HALLWAY = replace(
    KITCHEN,
    slug='hallway',
    name='Коридор',
    zone_slug='hallway',
    device_slug='thermostat-hallway',
    priority=4,
)
BOILER = LoadRecord(
    slug='boiler-1f',
    name='Бойлер 1 этаж',
    kind=LoadKind.PLUG,
    zone_slug=None,
    device_slug='plug-boiler-1f',
    priority=1,
    phase=LoadPhase.L2,
    nominal_amps=Decimal('6.0'),
    learned=None,
    live=True,
    shed=None,
)
FRESH = MeterWatch(seen=True, stale_since=None)
KITCHEN_START = ZoneStart(zone='kitchen', writes_live=True)
GRANTED = StartAnswer(granted=True, note=None)
WAITS = StartAnswer(granted=False, note=None)
HALLWAY_HEATING = SeenReading(
    polled_at=NOW,
    reading=thermostat_reading(
        power=True,
        heating=True,
        setpoint=Decimal('28.0'),
        floor_temperature=Decimal('24.5'),
        read_at=AT - 5,
    ),
)


def _remember(
    book: GuardBook,
    *,
    management_live: bool,
    meter: MeterWatch = FRESH,
    l2: str = '10.0',
    readings: dict[str, SeenReading] | None = None,
    shedding: ShedState | None = None,
) -> None:
    loads = GuardLoads(
        records=(BOILER, HALLWAY, KITCHEN, LIVING_ROOM),
        control=LoadControl(live=management_live, thresholds=THRESHOLDS, default_amps=Decimal('6.0')),
        live_zones=frozenset({'hallway', 'kitchen', 'living-room'}),
        meter='phase-meter',
        read_at=AT,
    )
    started = GuardState.started(loads, AT)
    currents = (
        {LoadPhase.L1: Decimal('8.0'), LoadPhase.L2: Decimal(l2), LoadPhase.L3: Decimal('4.0')}
        if meter.stale_since is None
        else {}
    )
    book.remember(
        replace(
            started,
            phases=replace(started.phases, meter=meter, currents=currents),
            readings={} if readings is None else readings,
            shedding=ShedState() if shedding is None else shedding,
        ),
    )


def _would_wait(phase: LoadPhase, amps: str | None, reason: StartRefusal) -> StartWouldWaitDryRun:
    return StartWouldWaitDryRun(
        load='kitchen',
        name='Кухня',
        phase=phase,
        amps=None if amps is None else Decimal(amps),
        load_amps=Decimal('8.0'),
        reason=reason,
        occurred_at=NOW,
    )


@pytest.fixture
def f_book() -> GuardBook:
    return GuardBook()


@pytest.fixture
def f_gate(f_book: GuardBook) -> GuardLoadGate:
    return GuardLoadGate(book=f_book, clock=MonotonicClockStub(now=AT))


@pytest.fixture
def f_live_gate(request: pytest.FixtureRequest, f_book: GuardBook, f_gate: GuardLoadGate) -> GuardLoadGate:
    _remember(f_book, management_live=True, l2=request.param)
    return f_gate


@pytest.fixture
def f_dry_run_gate(f_book: GuardBook, f_gate: GuardLoadGate) -> GuardLoadGate:
    _remember(f_book, management_live=False, l2='13.0')
    return f_gate


@pytest.fixture
def f_unseen_meter_gate(request: pytest.FixtureRequest, f_book: GuardBook, f_gate: GuardLoadGate) -> GuardLoadGate:
    _remember(f_book, management_live=request.param, meter=MeterWatch(seen=False, stale_since=AT - 10))
    return f_gate


@pytest.fixture
def f_lost_meter_gate(request: pytest.FixtureRequest, f_book: GuardBook, f_gate: GuardLoadGate) -> GuardLoadGate:
    readings = {'hallway': HALLWAY_HEATING} if request.param else {}
    _remember(f_book, management_live=True, meter=MeterWatch(seen=True, stale_since=AT - 60), readings=readings)
    return f_gate


@pytest.fixture
def f_stale_meter_gate(f_book: GuardBook, f_gate: GuardLoadGate) -> GuardLoadGate:
    _remember(f_book, management_live=False, meter=MeterWatch(seen=True, stale_since=AT - 10))
    return f_gate


@pytest.fixture
def f_shed_gate(f_book: GuardBook, f_gate: GuardLoadGate) -> GuardLoadGate:
    shed = Shed(
        load='kitchen',
        phase=LoadPhase.L2,
        amps=Decimal('8.0'),
        live=True,
        reason=ShedReason.OVERLOAD,
        was_on=None,
        at=AT - 30,
        settled_at=AT - 25,
    )
    _remember(f_book, management_live=True, shedding=ShedState(sheds={'kitchen': shed}))
    return f_gate


def test_gate_holds_the_zones_of_live_sheds(f_shed_gate: GuardLoadGate) -> None:
    assert f_shed_gate.held_zones() == frozenset({'kitchen'})


def test_every_start_goes_before_the_guard_has_watched_a_second(f_gate: GuardLoadGate, f_book: GuardBook) -> None:
    answers = f_gate.ask_starts([KITCHEN_START], NOW)

    assert (answers, f_book.last_permit_at()) == ({}, None)


@pytest.mark.parametrize('f_live_gate', ['10.0'], indirect=True)
def test_zone_without_a_load_is_not_gated(f_live_gate: GuardLoadGate) -> None:
    assert f_live_gate.ask_starts([ZoneStart(zone='attic', writes_live=True)], NOW) == {}


@pytest.mark.parametrize(
    ('f_live_gate', 'answer', 'last_permit_at'),
    [('12.0', GRANTED, AT), ('12.5', WAITS, None)],
    ids=['room_under_the_start_ceiling', 'no_room_under_the_start_ceiling'],
    indirect=['f_live_gate'],
)
def test_live_start_takes_a_permit_or_waits_for_room(
    f_live_gate: GuardLoadGate,
    f_book: GuardBook,
    answer: StartAnswer,
    last_permit_at: float | None,
) -> None:
    answers = f_live_gate.ask_starts([KITCHEN_START], NOW)

    assert (answers, f_book.last_permit_at()) == ({'kitchen': answer}, last_permit_at)


@pytest.mark.parametrize('f_live_gate', ['10.0'], indirect=True)
def test_live_starts_of_one_cycle_are_served_most_important_first(f_live_gate: GuardLoadGate) -> None:
    answers = f_live_gate.ask_starts([KITCHEN_START, ZoneStart(zone='living-room', writes_live=True)], NOW)

    assert answers == {'kitchen': WAITS, 'living-room': GRANTED}


@pytest.mark.parametrize('f_live_gate', ['10.0'], indirect=True)
def test_start_of_a_zone_whose_writes_are_held_back_only_checks_the_gate(
    f_live_gate: GuardLoadGate,
    f_book: GuardBook,
) -> None:
    answers = f_live_gate.ask_starts([ZoneStart(zone='kitchen', writes_live=False)], NOW)

    assert (answers, f_book.last_permit_at()) == ({'kitchen': GRANTED}, None)


def test_dry_run_start_that_would_wait_is_noted_once_a_waiting_episode(f_dry_run_gate: GuardLoadGate) -> None:
    first = f_dry_run_gate.ask_starts([KITCHEN_START], NOW)
    second = f_dry_run_gate.ask_starts([KITCHEN_START], NOW)

    assert (first, second) == (
        {'kitchen': StartAnswer(granted=True, note=_would_wait(LoadPhase.L2, '21.0', StartRefusal.NO_HEADROOM))},
        {'kitchen': GRANTED},
    )


def test_dry_run_start_that_waits_again_after_its_episode_ended_is_noted_again(f_dry_run_gate: GuardLoadGate) -> None:
    f_dry_run_gate.ask_starts([KITCHEN_START], NOW)
    f_dry_run_gate.ask_starts([], NOW)

    assert f_dry_run_gate.ask_starts([KITCHEN_START], NOW) == {
        'kitchen': StartAnswer(granted=True, note=_would_wait(LoadPhase.L2, '21.0', StartRefusal.NO_HEADROOM)),
    }


def test_dry_run_start_notes_a_meter_stale_since_its_last_reading(f_stale_meter_gate: GuardLoadGate) -> None:
    assert f_stale_meter_gate.ask_starts([KITCHEN_START], NOW) == {
        'kitchen': StartAnswer(granted=True, note=_would_wait(LoadPhase.L2, None, StartRefusal.METER_STALE)),
    }


@pytest.mark.parametrize(
    ('f_unseen_meter_gate', 'answers'),
    [(False, {}), (True, {'kitchen': WAITS})],
    ids=['dry_run_stays_quiet', 'live_waits'],
    indirect=['f_unseen_meter_gate'],
)
def test_meter_that_never_reported_stops_live_starts_and_keeps_dry_run_quiet(
    f_unseen_meter_gate: GuardLoadGate,
    answers: dict[str, StartAnswer],
) -> None:
    assert f_unseen_meter_gate.ask_starts([KITCHEN_START], NOW) == answers


@pytest.mark.parametrize(
    ('f_lost_meter_gate', 'answer'),
    [(False, GRANTED), (True, WAITS)],
    ids=['no_other_floor_draws_on_its_phase', 'another_floor_draws_on_its_phase'],
    indirect=['f_lost_meter_gate'],
)
def test_lost_meter_starts_a_floor_within_one_drawing_floor_a_phase(
    f_lost_meter_gate: GuardLoadGate,
    answer: StartAnswer,
) -> None:
    assert f_lost_meter_gate.ask_starts([KITCHEN_START], NOW) == {'kitchen': answer}
