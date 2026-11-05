from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.manual_change import ManualChange, source_change, teplo_window, thermostat_change
from apps.devices.domain.readings import ClimateMode, ClimateUnitState, ReadingPair, ThermostatMode, ThermostatState

NOW = datetime(2026, 11, 4, 16, 40, tzinfo=UTC)
FIRST_POLL = 1000.0
STATE = ThermostatState(
    power=True,
    mode=ThermostatMode.HOLD,
    setpoint=Decimal('28.0'),
    floor_temperature=Decimal('24.5'),
    heating=True,
    read_at=NOW,
)


CONVECTOR = ClimateUnitState(
    mode=ClimateMode.HEAT,
    target=Decimal(22),
    room_temperature=Decimal(21),
    read_at=NOW,
    level='High',
)
AIR_CONDITIONER = ClimateUnitState(
    mode=ClimateMode.HEAT,
    target=Decimal('20.5'),
    room_temperature=Decimal('19.5'),
    read_at=NOW,
)


def _source_pair(
    after: ClimateUnitState,
    *,
    span: float = 30.0,
    before: ClimateUnitState = CONVECTOR,
) -> ReadingPair[ClimateUnitState]:
    return ReadingPair(
        slug='heater-office',
        before=before,
        after=after,
        before_moment=FIRST_POLL,
        after_moment=FIRST_POLL + span,
    )


def _pair(after: ThermostatState, *, span: float = 30.0) -> ReadingPair[ThermostatState]:
    return ReadingPair(
        slug='thermostat-kitchen',
        before=STATE,
        after=after,
        before_moment=FIRST_POLL,
        after_moment=FIRST_POLL + span,
    )


@pytest.mark.parametrize(
    ('after', 'expected'),
    [
        (replace(STATE, power=False), ManualChange(power=(True, False), mode=None, setpoint=None)),
        (
            replace(STATE, mode=ThermostatMode.SMART),
            ManualChange(power=None, mode=('hold', 'smart'), setpoint=None),
        ),
        (
            replace(STATE, setpoint=Decimal('25.0')),
            ManualChange(power=None, mode=None, setpoint=(Decimal('28.0'), Decimal('25.0'))),
        ),
        (
            replace(STATE, power=False, setpoint=Decimal('25.0')),
            ManualChange(power=(True, False), mode=None, setpoint=(Decimal('28.0'), Decimal('25.0'))),
        ),
    ],
    ids=['power', 'mode', 'setpoint', 'power_and_setpoint'],
)
def test_change_of_power_mode_or_setpoint_between_two_polls_counts(
    after: ThermostatState,
    expected: ManualChange,
) -> None:
    assert thermostat_change(_pair(after)) == expected


@pytest.mark.parametrize(
    'after',
    [
        replace(STATE, heating=False),
        replace(STATE, floor_temperature=Decimal('26.0')),
        replace(STATE, read_at=NOW + timedelta(seconds=30)),
        STATE,
    ],
    ids=['relay', 'floor_temperature', 'read_later', 'nothing'],
)
def test_relay_and_floor_temperature_never_count(after: ThermostatState) -> None:
    assert thermostat_change(_pair(after)) is None


@pytest.mark.parametrize(
    ('span', 'counts'),
    [(90.0, True), (90.5, False)],
    ids=['ninety_seconds_apart', 'further_apart'],
)
def test_change_counts_only_between_polls_at_most_ninety_seconds_apart(span: float, counts: bool) -> None:
    change = thermostat_change(_pair(replace(STATE, setpoint=Decimal('25.0')), span=span))

    assert (change is not None) is counts


@pytest.mark.parametrize(
    ('checked_at', 'window'),
    [(FIRST_POLL + 50, (FIRST_POLL - 60, FIRST_POLL + 50)), (FIRST_POLL + 40, (FIRST_POLL - 60, FIRST_POLL + 45))],
    ids=['until_the_watch_read_what_teplo_stamped', 'at_least_to_the_second_poll'],
)
def test_teplo_window_runs_from_a_minute_before_the_first_poll_until_the_watch_read_the_stamps(
    checked_at: float,
    window: tuple[float, float],
) -> None:
    assert teplo_window(_pair(STATE, span=45.0), checked_at) == window


@pytest.mark.parametrize(
    ('after', 'expected'),
    [
        (replace(CONVECTOR, mode=ClimateMode.OFF), ManualChange(power=(True, False), mode=None, setpoint=None)),
        (
            replace(CONVECTOR, target=Decimal(25)),
            ManualChange(power=None, mode=None, setpoint=(Decimal(22), Decimal(25))),
        ),
    ],
    ids=['power', 'setpoint'],
)
def test_change_of_a_heat_source_counts_its_power_and_its_setpoint(
    after: ClimateUnitState,
    expected: ManualChange,
) -> None:
    assert source_change(_source_pair(after)) == expected


@pytest.mark.parametrize(
    'after',
    [
        replace(CONVECTOR, level='Low'),
        replace(CONVECTOR, room_temperature=Decimal(23)),
        CONVECTOR,
    ],
    ids=['level', 'own_temperature', 'nothing'],
)
def test_level_and_own_temperature_of_a_heat_source_never_count(after: ClimateUnitState) -> None:
    assert source_change(_source_pair(after)) is None


def test_change_of_a_heat_source_counts_only_between_polls_at_most_ninety_seconds_apart() -> None:
    assert source_change(_source_pair(replace(CONVECTOR, target=Decimal(25)), span=90.5)) is None


@pytest.mark.parametrize(
    ('before', 'after', 'expected'),
    [
        (
            AIR_CONDITIONER,
            replace(AIR_CONDITIONER, mode=ClimateMode.COOL),
            ManualChange(power=None, mode=('heat', 'cool'), setpoint=None),
        ),
        (
            AIR_CONDITIONER,
            replace(AIR_CONDITIONER, mode=ClimateMode.OTHER, target=Decimal(24)),
            ManualChange(power=None, mode=('heat', 'other'), setpoint=(Decimal('20.5'), Decimal(24))),
        ),
        (
            replace(AIR_CONDITIONER, mode=ClimateMode.AUTO),
            AIR_CONDITIONER,
            ManualChange(power=None, mode=('auto', 'heat'), setpoint=None),
        ),
        (
            replace(AIR_CONDITIONER, mode=ClimateMode.OFF),
            AIR_CONDITIONER,
            ManualChange(power=(False, True), mode=None, setpoint=None),
        ),
        (
            replace(AIR_CONDITIONER, mode=ClimateMode.COOL),
            replace(AIR_CONDITIONER, mode=ClimateMode.OFF),
            ManualChange(power=(True, False), mode=None, setpoint=None),
        ),
    ],
    ids=['heat_to_cool', 'heat_to_dry_and_set', 'auto_to_heat', 'switched_on_in_heat', 'cool_switched_off'],
)
def test_mode_changed_on_an_air_conditioner_counts_between_two_modes_it_runs_in(
    before: ClimateUnitState,
    after: ClimateUnitState,
    expected: ManualChange,
) -> None:
    assert source_change(_source_pair(after, before=before)) == expected
