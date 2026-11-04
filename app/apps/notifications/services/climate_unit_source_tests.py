from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import ClimateMode, ClimateUnitState
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import ClimateUnitStateStore
from apps.notifications.domain.alerts import MonitoredDevice
from apps.notifications.services.climate_unit_source import ClimateUnitSource

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 4, 17, 20, tzinfo=UTC)


def _state(read_at: datetime) -> ClimateUnitState:
    return ClimateUnitState(
        mode=ClimateMode.HEAT,
        target=Decimal(22),
        room_temperature=Decimal(21),
        read_at=read_at,
        level='High',
    )


@pytest.fixture
def f_source() -> ClimateUnitSource:
    DeviceFactory.create(slug='thermostat-kitchen')
    DeviceFactory.create(
        slug='heater-office',
        name='Конвектор: кабинет',
        kind=DeviceKind.CLIMATE_UNIT,
        driver=DeviceDriver.TUYA_HEATER,
    )
    DeviceFactory.create(
        slug='heater-bedroom',
        name='Обогреватель: спальня',
        kind=DeviceKind.CLIMATE_UNIT,
        driver=DeviceDriver.TUYA_HEATER,
    )
    clock = MonotonicClockStub(now=0.0)
    store = ClimateUnitStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_state('heater-office', _state(NOW - timedelta(minutes=11)))
    clock.advance(11 * 60)
    return ClimateUnitSource(devices=DeviceRepository(), store=store)


def test_climate_units_are_watched_under_their_device_names_by_the_age_of_their_last_reading(
    f_source: ClimateUnitSource,
) -> None:
    assert f_source.devices() == [
        MonitoredDevice(
            slug='heater-bedroom',
            name='Обогреватель: спальня',
            kind=DeviceKind.CLIMATE_UNIT,
            available=False,
            last_seen_age=None,
        ),
        MonitoredDevice(
            slug='heater-office',
            name='Конвектор: кабинет',
            kind=DeviceKind.CLIMATE_UNIT,
            available=False,
            last_seen_age=timedelta(minutes=11),
        ),
    ]
