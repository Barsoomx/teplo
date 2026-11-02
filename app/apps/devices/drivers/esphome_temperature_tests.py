from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.errors import DeviceUnavailableError
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
    ('f_unavailable_driver', 'detail'),
    [
        ('air-hallway', 'no recent reading'),
        ('outdoor', 'temperature: the node reported nan'),
        ('air-attic', 'no recent reading'),
    ],
    ids=['older-than-10-min', 'failed-without-a-reading', 'never-heard'],
    indirect=['f_unavailable_driver'],
)
def test_read_without_a_recent_reading_raises(f_unavailable_driver: EsphomeTemperatureDriver, detail: str) -> None:
    with pytest.raises(DeviceUnavailableError) as raised:
        f_unavailable_driver.read_temperature()

    assert (raised.value.error_code, raised.value.detail) == ('device_unavailable', detail)
