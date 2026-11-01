from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.factories import ZoneFactory
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import PlugState
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import PlugStateStore
from apps.notifications.domain.alerts import MonitoredDevice
from apps.notifications.services.plug_source import PlugSource

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)
CLOCK_STEP = timedelta(hours=5)


def _state(read_at: datetime) -> PlugState:
    return PlugState(
        on=True,
        power_watts=Decimal('1305.4'),
        current_amps=Decimal('6.02'),
        voltage_volts=Decimal('216.8'),
        read_at=read_at,
    )


@pytest.fixture
def f_source() -> PlugSource:
    ZoneFactory.create(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen')
    DeviceFactory.create(
        slug='plug-boiler-2f',
        name='Бойлер 2 этаж',
        kind=DeviceKind.PLUG,
        driver=DeviceDriver.TUYA_PLUG,
    )
    DeviceFactory.create(
        slug='plug-boiler-1f',
        name='Бойлер 1 этаж',
        kind=DeviceKind.PLUG,
        driver=DeviceDriver.TUYA_PLUG,
    )
    DeviceFactory.create(
        slug='plug-washer',
        name='Стиральная машина',
        kind=DeviceKind.PLUG,
        driver=DeviceDriver.TUYA_PLUG,
    )
    clock = MonotonicClockStub(now=0.0)
    store = PlugStateStore(unavailable_after=timedelta(minutes=10), clock=clock)
    store.record_state('plug-boiler-2f', _state(NOW - timedelta(minutes=11)))
    clock.advance(10 * 60)
    store.record_state('plug-boiler-1f', _state(NOW - CLOCK_STEP - timedelta(minutes=1)))
    clock.advance(60)
    return PlugSource(devices=DeviceRepository(), store=store)


def test_plugs_are_watched_under_their_device_names_by_the_age_of_their_last_reading(f_source: PlugSource) -> None:
    assert f_source.devices() == [
        MonitoredDevice(
            slug='plug-boiler-1f',
            name='Бойлер 1 этаж',
            kind=DeviceKind.PLUG,
            available=True,
            last_seen_age=timedelta(minutes=1),
        ),
        MonitoredDevice(
            slug='plug-boiler-2f',
            name='Бойлер 2 этаж',
            kind=DeviceKind.PLUG,
            available=False,
            last_seen_age=timedelta(minutes=11),
        ),
        MonitoredDevice(
            slug='plug-washer',
            name='Стиральная машина',
            kind=DeviceKind.PLUG,
            available=False,
            last_seen_age=None,
        ),
    ]
