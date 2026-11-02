from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import TemperatureReading
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import TemperatureStateStore
from apps.notifications.domain.alerts import MonitoredDevice
from apps.notifications.services.temperature_sensor_source import TemperatureSensorSource

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 2, 17, 40, tzinfo=UTC)


def _reading(read_at: datetime) -> TemperatureReading:
    return TemperatureReading(temperature=Decimal('21.5'), humidity=Decimal('41.2'), read_at=read_at)


@pytest.fixture
def f_source() -> TemperatureSensorSource:
    for slug, name in (('outdoor', 'Улица'), ('air-kitchen', 'Воздух: кухня'), ('air-hallway', 'Воздух: прихожая')):
        DeviceFactory.create(
            slug=slug,
            name=name,
            kind=DeviceKind.TEMPERATURE_SENSOR,
            driver=DeviceDriver.ESPHOME_TEMPERATURE,
        )
    DeviceFactory.create(
        slug='phase-meter',
        name='Фазометр',
        kind=DeviceKind.PHASE_METER,
        driver=DeviceDriver.ESPHOME_PHASE_METER,
    )
    DeviceFactory.create(slug='thermostat-kitchen', name='Кухня')
    clock = MonotonicClockStub(now=0.0)
    store = TemperatureStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_state('outdoor', _reading(NOW - timedelta(minutes=11)))
    clock.advance(10 * 60)
    store.record_state('air-kitchen', _reading(NOW - timedelta(minutes=1)))
    clock.advance(60)
    return TemperatureSensorSource(devices=DeviceRepository(), store=store)


def test_temperature_sensors_are_watched_under_their_device_names_by_the_age_of_their_last_reading(
    f_source: TemperatureSensorSource,
) -> None:
    assert f_source.devices() == [
        MonitoredDevice(
            slug='air-hallway',
            name='Воздух: прихожая',
            kind=DeviceKind.TEMPERATURE_SENSOR,
            available=False,
            last_seen_age=None,
        ),
        MonitoredDevice(
            slug='air-kitchen',
            name='Воздух: кухня',
            kind=DeviceKind.TEMPERATURE_SENSOR,
            available=True,
            last_seen_age=timedelta(minutes=1),
        ),
        MonitoredDevice(
            slug='outdoor',
            name='Улица',
            kind=DeviceKind.TEMPERATURE_SENSOR,
            available=False,
            last_seen_age=timedelta(minutes=11),
        ),
    ]
