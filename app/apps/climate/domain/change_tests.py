from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.climate.domain.change import ThermostatChange, plan_change
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.devices.domain.readings import ThermostatMode, ThermostatState

OFF = ThermostatTarget(power=False, setpoint=None)


@pytest.fixture
def f_hold_state() -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('22.0'),
        floor_temperature=Decimal('21.0'),
        read_at=datetime(2026, 10, 29, 2, 0, tzinfo=UTC),
    )


@pytest.fixture
def f_program_off_state(f_hold_state: ThermostatState) -> ThermostatState:
    return replace(f_hold_state, power=False, mode=ThermostatMode.SMART, setpoint=Decimal('5.0'))


def test_plan_change_is_empty_when_state_matches(f_hold_state: ThermostatState) -> None:
    assert plan_change(ThermostatTarget(power=True, setpoint=Decimal('22.0')), f_hold_state).is_empty


def test_plan_change_updates_only_setpoint(f_hold_state: ThermostatState) -> None:
    change = plan_change(ThermostatTarget(power=True, setpoint=Decimal('28.0')), f_hold_state)

    assert change == ThermostatChange(setpoint=Decimal('28.0'))


def test_plan_change_switches_smart_mode_to_hold(f_program_off_state: ThermostatState) -> None:
    change = plan_change(ThermostatTarget(power=True, setpoint=Decimal('18.0')), f_program_off_state)

    assert change == ThermostatChange(power=True, mode=ThermostatMode.HOLD, setpoint=Decimal('18.0'))


def test_plan_change_turns_off_only_powered_thermostat(
    f_hold_state: ThermostatState,
    f_program_off_state: ThermostatState,
) -> None:
    changes = (plan_change(OFF, f_hold_state), plan_change(OFF, f_program_off_state))

    assert changes == (ThermostatChange(power=False), ThermostatChange())
