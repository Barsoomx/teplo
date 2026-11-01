from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.services.state_store import ThermostatSnapshot, ThermostatStateStore

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=UTC)
CLOCK_STEP = timedelta(hours=5)


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def f_state() -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('22.0'),
        floor_temperature=Decimal('21.5'),
        read_at=NOW,
    )


@pytest.fixture
def f_store(clock: MonotonicClockStub, f_state: ThermostatState) -> ThermostatStateStore:
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_state('thermostat-kitchen', f_state)
    store.record_failure('thermostat-kitchen', error_code='device_unavailable', error='timeout')
    clock.advance(600)
    return store


@pytest.fixture
def f_stale_store(f_store: ThermostatStateStore, clock: MonotonicClockStub) -> ThermostatStateStore:
    clock.advance(1)
    return f_store


@pytest.fixture
def f_recovered_store(f_store: ThermostatStateStore, f_state: ThermostatState) -> ThermostatStateStore:
    f_store.record_state('thermostat-kitchen', replace(f_state, read_at=NOW + timedelta(minutes=10)))
    return f_store


@pytest.fixture
def f_stepped_store(clock: MonotonicClockStub, f_state: ThermostatState) -> ThermostatStateStore:
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_state('thermostat-kitchen', replace(f_state, read_at=NOW - CLOCK_STEP))
    clock.advance(30)
    return store


def test_failure_keeps_last_state_and_error(f_store: ThermostatStateStore, f_state: ThermostatState) -> None:
    assert f_store.snapshot('thermostat-kitchen') == ThermostatSnapshot(
        slug='thermostat-kitchen',
        state=f_state,
        age=timedelta(minutes=10),
        available=True,
        error_code='device_unavailable',
        error='timeout',
    )


def test_state_older_than_window_is_unavailable(f_stale_store: ThermostatStateStore) -> None:
    snapshot = f_stale_store.snapshot('thermostat-kitchen')

    assert (snapshot.age, snapshot.available) == (timedelta(minutes=10, seconds=1), False)


def test_success_clears_error(f_recovered_store: ThermostatStateStore) -> None:
    snapshot = f_recovered_store.snapshot('thermostat-kitchen')

    assert (snapshot.age, snapshot.error_code, snapshot.error) == (timedelta(0), None, None)


def test_reading_stamped_before_a_clock_step_is_aged_by_the_monotonic_clock(
    f_stepped_store: ThermostatStateStore,
) -> None:
    snapshot = f_stepped_store.snapshot('thermostat-kitchen')

    assert (snapshot.age, snapshot.available) == (timedelta(seconds=30), True)


def test_unknown_device_is_unavailable(f_store: ThermostatStateStore) -> None:
    assert f_store.snapshot('thermostat-attic') == ThermostatSnapshot(
        slug='thermostat-attic',
        state=None,
        age=None,
        available=False,
        error_code=None,
        error=None,
    )
