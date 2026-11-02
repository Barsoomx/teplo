from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.domain.errors import DomainError
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.readings import TemperatureReading
from apps.devices.drivers.esphome_temperature import EsphomeTemperatureDriver
from apps.devices.services.state_store import TemperatureStateStore

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def f_reading() -> TemperatureReading:
    return TemperatureReading(temperature=Decimal('21.5'), humidity=Decimal('41.2'), read_at=NOW)


@pytest.fixture
def store(f_reading: TemperatureReading) -> TemperatureStateStore:
    clock = MonotonicClockStub(now=1000.0)
    store = TemperatureStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_state('air-hallway', f_reading)
    store.record_state('air-living-room', f_reading)
    store.record_failure('air-living-room', error_code='broker_unavailable', error='mqtt broker unreachable')
    clock.advance(601)
    store.record_state('air-kitchen', f_reading)
    store.record_failure('outdoor', error_code='sensor_reading_failed', error='temperature: the node reported nan')
    return store


@pytest.fixture
def f_driver(store: TemperatureStateStore) -> EsphomeTemperatureDriver:
    return EsphomeTemperatureDriver(slug='air-kitchen', store=store)


@pytest.fixture
def f_unavailable_driver(store: TemperatureStateStore, request: pytest.FixtureRequest) -> EsphomeTemperatureDriver:
    return EsphomeTemperatureDriver(slug=request.param, store=store)


def test_read_gives_the_latest_reading(f_driver: EsphomeTemperatureDriver, f_reading: TemperatureReading) -> None:
    assert f_driver.read_temperature() == f_reading


@pytest.mark.parametrize(
    ('f_unavailable_driver', 'failure'),
    [
        ('air-hallway', ('device_unavailable', 'no recent reading')),
        ('air-living-room', ('broker_unavailable', 'mqtt broker unreachable')),
        ('outdoor', ('sensor_reading_failed', 'temperature: the node reported nan')),
        ('air-attic', ('device_unavailable', 'no recent reading')),
    ],
    ids=['older-than-10-min', 'older-than-10-min-after-a-failure', 'failed-without-a-reading', 'never-heard'],
    indirect=['f_unavailable_driver'],
)
def test_read_without_a_recent_reading_raises_the_recorded_failure_with_its_code(
    f_unavailable_driver: EsphomeTemperatureDriver,
    failure: tuple[str, str],
) -> None:
    with pytest.raises(DomainError) as raised:
        f_unavailable_driver.read_temperature()

    assert (raised.value.error_code, raised.value.detail) == failure
