from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.drive import StartAnswer, StartPermits, ZoneDrive, plan_drive
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.modes import ZoneCycleOutcome
from apps.devices.domain.readings import ThermostatMode, ThermostatState

NOW = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)
COMFORT = ThermostatTarget(power=True, setpoint=Decimal('28.0'))
NIGHT = ThermostatTarget(power=True, setpoint=Decimal('22.0'))
OFF = ThermostatTarget(power=False, setpoint=None)
IDLE = ThermostatState(
    power=True,
    mode=ThermostatMode.HOLD,
    setpoint=Decimal('22.0'),
    floor_temperature=Decimal('22.5'),
    heating=False,
    read_at=NOW,
)
HEATING = replace(IDLE, setpoint=Decimal('25.0'), heating=True)
SWITCHED_OFF = replace(IDLE, power=False)
PERMITS = StartPermits(
    answers={
        'kitchen': StartAnswer(granted=True, note=None),
        'hallway': StartAnswer(granted=False, note=None),
    },
    gated=frozenset({'kitchen', 'hallway', 'living-room'}),
)


def _settled(outcome: ZoneCycleOutcome) -> ZoneDrive:
    return ZoneDrive(change=ThermostatChange(), outcome=outcome, starts=False)


def _due(change: ThermostatChange, *, starts: bool) -> ZoneDrive:
    return ZoneDrive(change=change, outcome=None, starts=starts)


@pytest.mark.parametrize(
    ('state', 'held', 'last_write_at', 'expected'),
    [
        (IDLE, True, None, _settled(ZoneCycleOutcome.SHED)),
        (None, True, None, _settled(ZoneCycleOutcome.SHED)),
        (None, False, None, _settled(ZoneCycleOutcome.DEVICE_UNAVAILABLE)),
        (replace(IDLE, setpoint=Decimal('28.0')), False, None, _settled(ZoneCycleOutcome.UNCHANGED)),
        (IDLE, False, NOW - timedelta(minutes=29), _settled(ZoneCycleOutcome.THROTTLED)),
        (IDLE, False, NOW - timedelta(minutes=30), _due(ThermostatChange(setpoint=Decimal('28.0')), starts=True)),
    ],
    ids=[
        'shed_zone_is_held',
        'shed_zone_is_held_whatever_its_thermostat_reads',
        'unavailable_thermostat',
        'thermostat_already_on_target',
        'within_thirty_minutes_of_the_last_write',
        'thirty_minutes_after_the_last_write',
    ],
)
def test_drive_writes_only_an_allowed_change_to_a_zone_that_is_not_shed(
    state: ThermostatState | None,
    held: bool,
    last_write_at: datetime | None,
    expected: ZoneDrive,
) -> None:
    assert plan_drive(COMFORT, state, last_write_at=last_write_at, now=NOW, held=held) == expected


@pytest.mark.parametrize(
    ('target', 'state', 'expected'),
    [
        (NIGHT, SWITCHED_OFF, _due(ThermostatChange(power=True), starts=True)),
        (COMFORT, IDLE, _due(ThermostatChange(setpoint=Decimal('28.0')), starts=True)),
        (
            COMFORT,
            replace(IDLE, heating=None),
            _due(ThermostatChange(setpoint=Decimal('28.0')), starts=True),
        ),
        (COMFORT, HEATING, _due(ThermostatChange(setpoint=Decimal('28.0')), starts=False)),
        (
            COMFORT,
            replace(HEATING, heating=None),
            _due(ThermostatChange(setpoint=Decimal('28.0')), starts=False),
        ),
        (
            ThermostatTarget(power=True, setpoint=Decimal('22.5')),
            IDLE,
            _due(ThermostatChange(setpoint=Decimal('22.5')), starts=False),
        ),
        (NIGHT, HEATING, _due(ThermostatChange(setpoint=Decimal('22.0')), starts=False)),
        (OFF, IDLE, _due(ThermostatChange(power=False), starts=False)),
        (
            NIGHT,
            replace(IDLE, mode=ThermostatMode.SMART),
            _due(ThermostatChange(mode=ThermostatMode.HOLD), starts=False),
        ),
    ],
    ids=[
        'switching_a_thermostat_on',
        'raising_an_idle_thermostat_above_its_floor',
        'raising_a_thermostat_of_unknown_relay_set_below_its_floor',
        'raising_a_heating_thermostat',
        'raising_a_thermostat_of_unknown_relay_set_above_its_floor',
        'raising_an_idle_thermostat_only_to_its_floor',
        'lowering_the_setpoint',
        'switching_off',
        'switching_to_hold_only',
    ],
)
def test_write_starts_a_zone_that_draws_nothing_and_would_heat(
    target: ThermostatTarget,
    state: ThermostatState,
    expected: ZoneDrive,
) -> None:
    assert plan_drive(target, state, last_write_at=None, now=NOW, held=False) == expected


@pytest.mark.parametrize(
    ('zone', 'writes_live', 'expected'),
    [
        ('kitchen', True, True),
        ('hallway', True, False),
        ('living-room', True, False),
        ('living-room', False, True),
        ('bathroom-1f', True, True),
    ],
    ids=[
        'start_granted_when_asked',
        'start_refused_when_asked',
        'gated_start_it_did_not_ask_for',
        'start_it_did_not_ask_for_whose_writes_are_held_back',
        'start_load_management_does_not_gate',
    ],
)
def test_start_goes_with_the_answer_it_got_and_without_one_only_when_no_live_permit_gates_it(
    zone: str,
    writes_live: bool,
    expected: bool,
) -> None:
    assert PERMITS.granted(zone, writes_live=writes_live) is expected
