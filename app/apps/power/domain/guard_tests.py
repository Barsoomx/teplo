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
    follow_samples,
    load_statuses,
    relieved_currents,
)
from apps.power.domain.load_status import LoadStatus, plug_reading, thermostat_reading
from apps.power.domain.loads import LearnedPhase, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.phase_samples import PhaseSample
from apps.power.domain.shedding import Shed

AT = 1000.0
POLLED_AT = datetime(2026, 11, 2, 18, 40, tzinfo=UTC)
CURRENTS = {LoadPhase.L1: Decimal('12.0'), LoadPhase.L2: Decimal('26.0'), LoadPhase.L3: Decimal('4.0')}
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


def test_samples_keep_the_last_two_minutes_and_add_the_currents_of_the_second(
    f_samples: tuple[PhaseSample, ...],
) -> None:
    assert follow_samples(f_samples, CURRENTS, AT) == (_sample(AT - 120), _sample(AT - 1), _sample(AT))


def test_second_without_every_phase_adds_no_sample(f_samples: tuple[PhaseSample, ...]) -> None:
    currents = {LoadPhase.L1: Decimal('12.0'), LoadPhase.L3: Decimal('4.0')}

    assert follow_samples(f_samples, currents, AT) == (_sample(AT - 120), _sample(AT - 1))


def test_statuses_follow_the_readings_and_the_sheds() -> None:
    statuses = load_statuses(
        LOADS,
        {'boiler-1f': BOILER_ON, 'kitchen': KITCHEN_HEATING},
        ShedState(sheds={'kitchen': SHED}),
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
            shed=False,
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
    statuses = load_statuses(LOADS, {'boiler-1f': BOILER_ON}, ShedState(failed={'boiler-1f': failed_at}))

    assert (statuses[0].available, statuses[0].drawing) == (available, available)


def test_relieved_currents_take_the_relief_of_the_sheds_from_their_phase() -> None:
    assert relieved_currents(CURRENTS, [SHED], AT) == {
        LoadPhase.L1: Decimal('12.0'),
        LoadPhase.L2: Decimal('18.0'),
        LoadPhase.L3: Decimal('4.0'),
    }


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
