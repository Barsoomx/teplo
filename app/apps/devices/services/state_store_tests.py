from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.services.state_store import ThermostatSnapshot, ThermostatStateStore

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)


@pytest.fixture
def f_state() -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('22.0'),
        floor_temperature=Decimal('21.5'),
        read_at=NOW - timedelta(minutes=10),
    )


@pytest.fixture
def f_store(f_state: ThermostatState) -> ThermostatStateStore:
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_state('thermostat-kitchen', f_state)
    store.record_failure('thermostat-kitchen', 'timeout')
    return store


@pytest.fixture
def f_recovered_store(f_store: ThermostatStateStore, f_state: ThermostatState) -> ThermostatStateStore:
    f_store.record_state('thermostat-kitchen', replace(f_state, read_at=NOW))
    return f_store


def test_failure_keeps_last_state_and_error(f_store: ThermostatStateStore, f_state: ThermostatState) -> None:
    assert f_store.snapshot('thermostat-kitchen', NOW) == ThermostatSnapshot(
        slug='thermostat-kitchen',
        state=f_state,
        available=True,
        error='timeout',
    )


def test_state_older_than_window_is_unavailable(f_store: ThermostatStateStore) -> None:
    assert f_store.snapshot('thermostat-kitchen', NOW + timedelta(minutes=1)).available is False


def test_success_clears_error(f_recovered_store: ThermostatStateStore) -> None:
    assert f_recovered_store.snapshot('thermostat-kitchen', NOW).error is None


def test_unknown_device_is_unavailable(f_store: ThermostatStateStore) -> None:
    assert f_store.snapshot('thermostat-attic', NOW) == ThermostatSnapshot(
        slug='thermostat-attic',
        state=None,
        available=False,
        error=None,
    )
