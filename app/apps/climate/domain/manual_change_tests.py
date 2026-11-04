from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.manual_change import ManualChange, teplo_window, thermostat_change
from apps.devices.domain.readings import ReadingPair, ThermostatMode, ThermostatState

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


def test_teplo_window_runs_from_a_minute_before_the_first_poll_to_the_second() -> None:
    assert teplo_window(_pair(STATE, span=45.0)) == (FIRST_POLL - 60, FIRST_POLL + 45)
