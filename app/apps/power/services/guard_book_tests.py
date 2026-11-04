from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.power.domain.control import LoadControl, PhaseThresholds
from apps.power.domain.guard import GuardLoads, GuardState, ShedState
from apps.power.domain.headroom import PhaseOverrun
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.meter_watch import MeterCondition
from apps.power.domain.shedding import Shed
from apps.power.domain.start_gate import StartDecision, StartRefusal, StartRequest
from apps.power.domain.view import GuardView
from apps.power.services.guard_book import GuardBook

AT = 1000.0
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
CURRENTS = {LoadPhase.L1: Decimal('12.0'), LoadPhase.L2: Decimal('15.0'), LoadPhase.L3: Decimal('9.0')}
ZONE_LOADS = frozenset({'kitchen', 'hallway'})
SOURCE_LOADS = frozenset({'convector-office'})
KITCHEN = LoadStatus(
    slug='kitchen',
    kind=LoadKind.ZONE,
    priority=5,
    phase=LoadPhase.L1,
    amps=Decimal('8.0'),
    available=True,
    drawing=False,
    shed=False,
)
KITCHEN_RECORD = LoadRecord(
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug='thermostat-kitchen',
    priority=5,
    phase=LoadPhase.L1,
    nominal_amps=Decimal('8.0'),
    learned=None,
    live=True,
    shed=None,
)
KITCHEN_SHED = Shed(
    load='kitchen',
    phase=LoadPhase.L1,
    amps=Decimal('8.0'),
    live=True,
    reason=ShedReason.OVERLOAD,
    was_on=None,
    device='thermostat-kitchen',
    at=AT,
    occurred_at=datetime(2026, 11, 2, 18, 40, tzinfo=UTC),
    settled_at=AT + 5,
)


def _permit(book: GuardBook, load: LoadStatus, at: float) -> StartDecision:
    return book.permit_start(
        load,
        meter=MeterCondition.FRESH,
        view=GuardView(currents=CURRENTS, loads=(load,)),
        at=at,
        thresholds=THRESHOLDS,
    )


def _serve(book: GuardBook, requests: tuple[StartRequest, ...], at: float) -> dict[str, StartDecision]:
    view = GuardView(currents=CURRENTS, loads=tuple(request.load for request in requests))
    return dict(
        book.serve_starts(
            requests,
            meter=MeterCondition.FRESH,
            live_view=view,
            dry_run_view=view,
            at=at,
            thresholds=THRESHOLDS,
        ),
    )


@pytest.fixture
def f_book() -> GuardBook:
    return GuardBook()


@pytest.fixture
def f_book_after_a_permit(f_book: GuardBook) -> GuardBook:
    _permit(f_book, KITCHEN, AT)
    return f_book


@pytest.fixture
def f_book_after_rounds_of_starts(f_book: GuardBook) -> GuardBook:
    hallway = replace(KITCHEN, slug='hallway', priority=4)
    living_room = replace(KITCHEN, slug='living-room', priority=7)
    _serve(f_book, (StartRequest(load=hallway, live=True), StartRequest(load=KITCHEN, live=False)), AT)
    _serve(f_book, (StartRequest(load=living_room, live=True),), AT + 10)
    _permit(f_book, replace(KITCHEN, slug='boiler-1f', kind=LoadKind.PLUG, priority=1), AT + 30)
    return f_book


@pytest.fixture
def f_book_with_a_waiting_load(f_book: GuardBook) -> GuardBook:
    f_book.note_waiting(ZONE_LOADS, frozenset({'kitchen'}))
    return f_book


@pytest.fixture
def f_book_with_a_waiting_zone_and_source(f_book_with_a_waiting_load: GuardBook) -> GuardBook:
    f_book_with_a_waiting_load.note_waiting(SOURCE_LOADS, frozenset({'convector-office'}))
    return f_book_with_a_waiting_load


@pytest.fixture
def f_book_with_a_shed_source(f_book: GuardBook) -> GuardBook:
    convector = replace(
        KITCHEN_RECORD,
        slug='convector-office',
        kind=LoadKind.SOURCE,
        zone_slug=None,
        device_slug='heater-office',
        source_slug='convector-office',
    )
    loads = GuardLoads(
        records=(KITCHEN_RECORD, convector),
        control=LoadControl(live=True, thresholds=THRESHOLDS, default_amps=Decimal('6.0')),
        live_zones=frozenset({'kitchen'}),
        meter='phase-meter',
        read_at=AT,
        live_sources=frozenset({'convector-office'}),
    )
    shed = replace(KITCHEN_SHED, load='convector-office', was_on=True, device='heater-office')
    f_book.remember(
        replace(GuardState.started(loads, AT), shedding=ShedState(sheds={'convector-office': shed})),
    )
    return f_book


@pytest.fixture
def f_book_with_a_shed_zone(f_book: GuardBook) -> GuardBook:
    loads = GuardLoads(
        records=(KITCHEN_RECORD,),
        control=LoadControl(live=True, thresholds=THRESHOLDS, default_amps=Decimal('6.0')),
        live_zones=frozenset({'kitchen'}),
        meter='phase-meter',
        read_at=AT,
    )
    f_book.remember(replace(GuardState.started(loads, AT), shedding=ShedState(sheds={'kitchen': KITCHEN_SHED})))
    return f_book


def test_granted_start_is_the_last_permit(f_book: GuardBook) -> None:
    decision = _permit(f_book, KITCHEN, AT)

    assert (decision, f_book.last_permit_at()) == (StartDecision(load='kitchen', refusal=None, overrun=None), AT)


def test_next_start_waits_thirty_seconds_after_the_last_permit(f_book_after_a_permit: GuardBook) -> None:
    decision = _permit(f_book_after_a_permit, replace(KITCHEN, slug='hallway'), AT + 10)

    assert (decision, f_book_after_a_permit.last_permit_at()) == (
        StartDecision(load='hallway', refusal=StartRefusal.TOO_SOON, overrun=None),
        AT,
    )


def test_refused_start_leaves_the_last_permit(f_book: GuardBook) -> None:
    decision = _permit(f_book, replace(KITCHEN, amps=Decimal('8.5')), AT)

    assert (decision, f_book.last_permit_at()) == (
        StartDecision(
            load='kitchen',
            refusal=StartRefusal.NO_HEADROOM,
            overrun=PhaseOverrun(phase=LoadPhase.L1, amps=Decimal('20.5')),
        ),
        None,
    )


def test_book_holds_the_zones_of_live_sheds(f_book_with_a_shed_zone: GuardBook) -> None:
    assert f_book_with_a_shed_zone.held_zones() == frozenset({'kitchen'})


def test_book_holds_no_zone_before_the_guard_runs(f_book: GuardBook) -> None:
    assert (f_book.state(), f_book.held_zones(), f_book.held_sources()) == (None, frozenset(), frozenset())


def test_book_holds_the_heat_sources_of_live_sheds_apart_from_the_zones(f_book_with_a_shed_source: GuardBook) -> None:
    assert (f_book_with_a_shed_source.held_sources(), f_book_with_a_shed_source.held_zones()) == (
        frozenset({'convector-office'}),
        frozenset(),
    )


def test_round_of_starts_takes_the_permit_of_its_live_start(f_book: GuardBook) -> None:
    hallway = replace(KITCHEN, slug='hallway', priority=4)

    decisions = _serve(
        f_book,
        (StartRequest(load=hallway, live=True), StartRequest(load=KITCHEN, live=False)),
        AT,
    )

    assert (decisions, f_book.last_permit_at()) == (
        {
            'kitchen': StartDecision(load='kitchen', refusal=None, overrun=None),
            'hallway': StartDecision(load='hallway', refusal=None, overrun=None),
        },
        AT,
    )


def test_book_remembers_when_each_granted_live_start_and_restore_was_permitted(
    f_book_after_rounds_of_starts: GuardBook,
) -> None:
    assert f_book_after_rounds_of_starts.live_starts() == {'hallway': AT, 'boiler-1f': AT + 30}


def test_round_of_dry_run_starts_leaves_the_last_permit(f_book_after_a_permit: GuardBook) -> None:
    decisions = _serve(f_book_after_a_permit, (StartRequest(load=KITCHEN, live=False),), AT + 30)

    assert (decisions, f_book_after_a_permit.last_permit_at()) == (
        {'kitchen': StartDecision(load='kitchen', refusal=None, overrun=None)},
        AT,
    )


@pytest.mark.parametrize(
    ('waiting', 'began'),
    [
        (frozenset({'kitchen'}), frozenset()),
        (frozenset({'kitchen', 'hallway'}), frozenset({'hallway'})),
        (frozenset(), frozenset()),
    ],
    ids=['still_waiting', 'another_begins_to_wait', 'none_waits'],
)
def test_book_names_only_the_loads_that_begin_to_wait(
    f_book_with_a_waiting_load: GuardBook,
    waiting: frozenset[str],
    began: frozenset[str],
) -> None:
    assert f_book_with_a_waiting_load.note_waiting(ZONE_LOADS, waiting) == began


def test_book_names_the_loads_still_waiting_to_start(f_book_with_a_waiting_load: GuardBook) -> None:
    assert f_book_with_a_waiting_load.waiting() == frozenset({'kitchen'})


def test_load_that_stopped_waiting_begins_a_new_wait(f_book_with_a_waiting_load: GuardBook) -> None:
    f_book_with_a_waiting_load.note_waiting(ZONE_LOADS, frozenset())

    assert f_book_with_a_waiting_load.note_waiting(ZONE_LOADS, frozenset({'kitchen'})) == frozenset({'kitchen'})


def test_each_cycle_ends_only_the_waiting_episodes_of_its_own_loads(
    f_book_with_a_waiting_zone_and_source: GuardBook,
) -> None:
    began = f_book_with_a_waiting_zone_and_source.note_waiting(ZONE_LOADS, frozenset())

    assert (began, f_book_with_a_waiting_zone_and_source.waiting()) == (frozenset(), frozenset({'convector-office'}))


def test_a_source_still_waiting_begins_no_new_episode_after_the_zones_asked(
    f_book_with_a_waiting_zone_and_source: GuardBook,
) -> None:
    f_book_with_a_waiting_zone_and_source.note_waiting(ZONE_LOADS, frozenset())

    began = f_book_with_a_waiting_zone_and_source.note_waiting(SOURCE_LOADS, frozenset({'convector-office'}))

    assert began == frozenset()
