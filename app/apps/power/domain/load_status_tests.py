from decimal import Decimal

import pytest

from apps.power.domain.load_status import LoadReading, LoadStatus, plug_reading, source_reading, thermostat_reading
from apps.power.domain.loads import LearnedPhase, LoadKind, LoadPhase, LoadRecord

READ_AT = 1000.0
DEFAULT_AMPS = Decimal('6.0')
KITCHEN = LoadRecord(
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug='thermostat-kitchen',
    priority=5,
    phase=LoadPhase.UNKNOWN,
    nominal_amps=None,
    learned=LearnedPhase(phase=LoadPhase.L3, amps=Decimal('8.2'), samples=3),
    live=False,
    shed=None,
)


@pytest.fixture
def f_heating_reading() -> LoadReading:
    return thermostat_reading(
        power=True,
        heating=True,
        setpoint=Decimal('28.0'),
        floor_temperature=Decimal('24.5'),
        read_at=READ_AT,
    )


@pytest.mark.parametrize(
    ('on', 'current', 'drawing'),
    [(True, Decimal('6.1'), True), (True, Decimal('0.5'), False), (False, Decimal('0.0'), False)],
    ids=['on_and_drawing', 'on_at_the_drawing_threshold', 'off'],
)
def test_plug_draws_when_on_and_over_half_an_ampere(on: bool, current: Decimal, drawing: bool) -> None:
    assert plug_reading(on=on, current=current, read_at=READ_AT) == LoadReading(
        read_at=READ_AT,
        drawing=drawing,
        closed=on,
        current=current,
    )


@pytest.mark.parametrize(
    ('power', 'heating', 'setpoint', 'drawing', 'closed'),
    [
        (True, True, Decimal('28.0'), True, True),
        (True, False, Decimal('28.0'), False, False),
        (False, True, Decimal('28.0'), False, False),
        (True, None, Decimal('28.0'), True, None),
        (True, None, Decimal('24.5'), False, None),
        (True, None, Decimal('18.0'), False, None),
        (False, None, Decimal('28.0'), False, None),
    ],
    ids=[
        'relay_heating',
        'relay_idle',
        'switched_off',
        'relay_unknown_setpoint_above_the_floor',
        'relay_unknown_setpoint_at_the_floor',
        'relay_unknown_setpoint_below_the_floor',
        'relay_unknown_switched_off',
    ],
)
def test_thermostat_draws_while_powered_and_heating_or_set_above_its_floor(
    power: bool,
    heating: bool | None,
    setpoint: Decimal,
    drawing: bool,
    closed: bool | None,
) -> None:
    reading = thermostat_reading(
        power=power,
        heating=heating,
        setpoint=setpoint,
        floor_temperature=Decimal('24.5'),
        read_at=READ_AT,
    )

    assert reading == LoadReading(read_at=READ_AT, drawing=drawing, closed=closed, current=None)


@pytest.mark.parametrize('on', [True, False], ids=['on', 'off'])
def test_heat_source_draws_whenever_it_is_on(on: bool) -> None:
    assert source_reading(on=on, read_at=READ_AT) == LoadReading(read_at=READ_AT, drawing=on, closed=on, current=None)


def test_status_of_a_load_with_a_reading_takes_its_effective_phase_and_amps(f_heating_reading: LoadReading) -> None:
    assert LoadStatus.of(KITCHEN, f_heating_reading, shed=False, default_amps=DEFAULT_AMPS) == LoadStatus(
        slug='kitchen',
        kind=LoadKind.ZONE,
        priority=5,
        phase=LoadPhase.L3,
        amps=Decimal('8.2'),
        available=True,
        drawing=True,
        shed=False,
    )


def test_status_of_a_load_without_a_reading_is_unavailable_and_not_drawing() -> None:
    assert LoadStatus.of(KITCHEN, None, shed=True, default_amps=DEFAULT_AMPS) == LoadStatus(
        slug='kitchen',
        kind=LoadKind.ZONE,
        priority=5,
        phase=LoadPhase.L3,
        amps=Decimal('8.2'),
        available=False,
        drawing=False,
        shed=True,
    )
