from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.power.domain.control import LoadControl, PhaseThresholds
from apps.power.domain.guard import (
    GuardLoads,
    GuardState,
    SeenReading,
    ShedState,
    awaits_reading,
    follow_samples,
    guard_view,
    load_statuses,
    phase_amps,
    relieved_currents,
    start_view,
)
from apps.power.domain.load_status import LoadStatus, plug_reading, thermostat_reading
from apps.power.domain.loads import LearnedPhase, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.phase_samples import PhaseSample
from apps.power.domain.phase_watch import OverloadWatch, PhaseAmps, PhaseWatch
from apps.power.domain.shedding import Shed

AT = 1000.0
POLLED_AT = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)
CURRENTS = {LoadPhase.L1: Decimal('12.0'), LoadPhase.L2: Decimal('26.0'), LoadPhase.L3: Decimal('4.0')}
READ_AT = {LoadPhase.L1: AT, LoadPhase.L2: AT, LoadPhase.L3: AT}
CONTROL = LoadControl(
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
)
BOILER = LoadRecord(
    slug='boiler-1f',
    name='Бойлер 1 этаж',
    kind=LoadKind.PLUG,
    zone_slug=None,
    device_slug='plug-boiler-1f',
    priority=1,
    phase=LoadPhase.L2,
    nominal_amps=None,
    learned=None,
    live=True,
    shed=None,
)
KITCHEN = replace(
    BOILER,
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug='thermostat-kitchen',
    priority=5,
    nominal_amps=Decimal('8.0'),
)
LOADS = GuardLoads(records=(BOILER, KITCHEN), control=CONTROL, live_zones=frozenset(), meter='phase-meter', read_at=AT)
SHED = Shed(
    load='kitchen',
    phase=LoadPhase.L2,
    amps=Decimal('8.0'),
    live=True,
    reason=ShedReason.OVERLOAD,
    was_on=None,
    at=AT - 2,
    settled_at=AT + 3,
)
BOILER_DRY_RUN_SHED = replace(SHED, load='boiler-1f', amps=Decimal('6.0'), live=False, was_on=True)
BOILER_ON = SeenReading(polled_at=POLLED_AT, reading=plug_reading(on=True, current=Decimal('6.1'), read_at=AT - 20))
KITCHEN_HEATING = SeenReading(
    polled_at=POLLED_AT,
    reading=thermostat_reading(
        power=True,
        heating=True,
        setpoint=Decimal('28.0'),
        floor_temperature=Decimal('24.5'),
        read_at=AT - 10,
    ),
)


def _sample(at: float) -> PhaseSample:
    return PhaseSample(at=at, amps=CURRENTS)


@pytest.fixture
def f_samples() -> tuple[PhaseSample, ...]:
    return tuple(_sample(at) for at in (AT - 121, AT - 120, AT - 1))


@pytest.fixture
def f_state_with_a_live_and_a_dry_run_shed() -> GuardState:
    started = GuardState.started(LOADS, AT)
    return replace(
        started,
        phases=replace(started.phases, currents=CURRENTS, read_at=READ_AT),
        readings={'boiler-1f': BOILER_ON, 'kitchen': KITCHEN_HEATING},
        shedding=ShedState(sheds={'boiler-1f': BOILER_DRY_RUN_SHED, 'kitchen': SHED}),
    )


@pytest.fixture
def f_state_with_overloaded_phases(f_state_with_a_live_and_a_dry_run_shed: GuardState) -> GuardState:
    return replace(
        f_state_with_a_live_and_a_dry_run_shed,
        phases=replace(
            f_state_with_a_live_and_a_dry_run_shed.phases,
            watches={
                LoadPhase.L1: PhaseWatch(),
                LoadPhase.L2: PhaseWatch(live=OverloadWatch(overloaded=True)),
                LoadPhase.L3: PhaseWatch(dry_run=OverloadWatch(overloaded=True)),
            },
        ),
    )


def test_samples_keep_the_last_two_minutes_and_add_the_currents_of_the_second(
    f_samples: tuple[PhaseSample, ...],
) -> None:
    assert follow_samples(f_samples, CURRENTS, AT) == (_sample(AT - 120), _sample(AT - 1), _sample(AT))


def test_second_without_every_phase_adds_no_sample(f_samples: tuple[PhaseSample, ...]) -> None:
    currents = {LoadPhase.L1: Decimal('12.0'), LoadPhase.L3: Decimal('4.0')}

    assert follow_samples(f_samples, currents, AT) == (_sample(AT - 120), _sample(AT - 1))


@pytest.mark.parametrize(
    ('live', 'boiler_shed'),
    [(True, False), (False, True)],
    ids=['live_view_sees_only_live_sheds', 'dry_run_view_sees_every_shed'],
)
def test_statuses_follow_the_readings_and_the_sheds_of_their_view(live: bool, boiler_shed: bool) -> None:
    statuses = load_statuses(
        LOADS,
        {'boiler-1f': BOILER_ON, 'kitchen': KITCHEN_HEATING},
        ShedState(sheds={'boiler-1f': BOILER_DRY_RUN_SHED, 'kitchen': SHED}),
        live=live,
    )

    assert statuses == (
        LoadStatus(
            slug='boiler-1f',
            kind=LoadKind.PLUG,
            priority=1,
            phase=LoadPhase.L2,
            amps=Decimal('6.0'),
            available=True,
            drawing=True,
            shed=boiler_shed,
        ),
        LoadStatus(
            slug='kitchen',
            kind=LoadKind.ZONE,
            priority=5,
            phase=LoadPhase.L2,
            amps=Decimal('8.0'),
            available=True,
            drawing=True,
            shed=True,
        ),
    )


@pytest.mark.parametrize(
    ('failed_at', 'available'),
    [(AT - 20, False), (AT - 30, True)],
    ids=['read_before_the_failed_command', 'read_after_the_failed_command'],
)
def test_load_whose_command_failed_counts_as_unavailable_until_it_is_read_again(
    failed_at: float,
    available: bool,
) -> None:
    statuses = load_statuses(LOADS, {'boiler-1f': BOILER_ON}, ShedState(failed={'boiler-1f': failed_at}), live=True)

    assert (statuses[0].available, statuses[0].drawing) == (available, available)


def test_relieved_currents_take_the_relief_of_the_sheds_from_their_phase() -> None:
    assert relieved_currents(CURRENTS, READ_AT, [SHED]) == {
        LoadPhase.L1: Decimal('12.0'),
        LoadPhase.L2: Decimal('18.0'),
        LoadPhase.L3: Decimal('4.0'),
    }


@pytest.mark.parametrize(
    ('live', 'l2'),
    [(True, Decimal('18.0')), (False, Decimal('12.0'))],
    ids=['live_view_takes_only_live_sheds', 'dry_run_view_takes_every_shed'],
)
def test_view_takes_from_the_measured_current_the_relief_of_the_sheds_it_sees(
    f_state_with_a_live_and_a_dry_run_shed: GuardState,
    live: bool,
    l2: Decimal,
) -> None:
    view = guard_view(
        f_state_with_a_live_and_a_dry_run_shed,
        f_state_with_a_live_and_a_dry_run_shed.shedding,
        live=live,
    )

    assert view.currents == {LoadPhase.L1: Decimal('12.0'), LoadPhase.L2: l2, LoadPhase.L3: Decimal('4.0')}


@pytest.mark.parametrize(
    ('live', 'l2'),
    [(True, Decimal('26.0')), (False, Decimal('20.0'))],
    ids=['live_start_on_the_measured_current', 'dry_run_start_less_the_dry_run_sheds'],
)
def test_start_view_credits_no_live_shed_whose_drop_the_meter_has_not_shown(
    f_state_with_a_live_and_a_dry_run_shed: GuardState,
    live: bool,
    l2: Decimal,
) -> None:
    view = start_view(
        f_state_with_a_live_and_a_dry_run_shed,
        f_state_with_a_live_and_a_dry_run_shed.shedding,
        live=live,
    )

    assert (view.currents, view.loads) == (
        {LoadPhase.L1: Decimal('12.0'), LoadPhase.L2: l2, LoadPhase.L3: Decimal('4.0')},
        guard_view(
            f_state_with_a_live_and_a_dry_run_shed,
            f_state_with_a_live_and_a_dry_run_shed.shedding,
            live=live,
        ).loads,
    )


@pytest.mark.parametrize(
    ('live', 'overloaded'),
    [(True, frozenset({LoadPhase.L2})), (False, frozenset({LoadPhase.L3}))],
    ids=['live_view', 'dry_run_view'],
)
def test_views_name_the_phases_overloaded_in_their_view(
    f_state_with_overloaded_phases: GuardState,
    live: bool,
    overloaded: frozenset[LoadPhase],
) -> None:
    assert (
        guard_view(f_state_with_overloaded_phases, f_state_with_overloaded_phases.shedding, live=live).overloaded,
        start_view(f_state_with_overloaded_phases, f_state_with_overloaded_phases.shedding, live=live).overloaded,
    ) == (overloaded, overloaded)


def test_phase_amps_give_each_phase_as_measured_and_as_each_view_sees_it_with_the_moment_it_was_read() -> None:
    assert phase_amps(CURRENTS, READ_AT, [SHED, BOILER_DRY_RUN_SHED]) == {
        LoadPhase.L1: PhaseAmps(measured=Decimal('12.0'), live=Decimal('12.0'), dry_run=Decimal('12.0'), read_at=AT),
        LoadPhase.L2: PhaseAmps(measured=Decimal('26.0'), live=Decimal('18.0'), dry_run=Decimal('12.0'), read_at=AT),
        LoadPhase.L3: PhaseAmps(measured=Decimal('4.0'), live=Decimal('4.0'), dry_run=Decimal('4.0'), read_at=AT),
    }


@pytest.mark.parametrize(
    ('failed', 'expected'),
    [({}, False), ({'boiler-1f': AT - 20}, True), ({'boiler-1f': AT - 30}, False)],
    ids=['no_failed_command', 'no_reading_since_the_failed_command', 'read_again_after_the_failed_command'],
)
def test_load_awaits_a_reading_after_a_failed_command(
    f_state_with_a_live_and_a_dry_run_shed: GuardState,
    failed: dict[str, float],
    expected: bool,
) -> None:
    assert awaits_reading(f_state_with_a_live_and_a_dry_run_shed, ShedState(failed=failed), 'boiler-1f') is expected


@pytest.mark.parametrize(
    ('shed', 'expected'),
    [(SHED, frozenset({'kitchen'})), (replace(SHED, live=False), frozenset())],
    ids=['live_shed', 'dry_run_shed'],
)
def test_zones_held_are_those_of_live_sheds(shed: Shed, expected: frozenset[str]) -> None:
    state = replace(GuardState.started(LOADS, AT), shedding=ShedState(sheds={'kitchen': shed}))

    assert state.held_zones == expected


def test_learned_phase_replaces_only_its_load() -> None:
    learned = LearnedPhase(phase=LoadPhase.L3, amps=Decimal('8.4'), samples=3)

    assert LOADS.with_learned('kitchen', learned).records == (BOILER, replace(KITCHEN, learned=learned))
