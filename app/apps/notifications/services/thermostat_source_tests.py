from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.factories import ZoneFactory
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.state_store import ThermostatStateStore
from apps.notifications.domain.alerts import MonitoredDevice
from apps.notifications.services.thermostat_source import ThermostatSource

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)


def _state(read_at: datetime) -> ThermostatState:
    return ThermostatState(
        power=True,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('22.0'),
        floor_temperature=Decimal('21.5'),
        read_at=read_at,
    )


@pytest.fixture
def f_source() -> ThermostatSource:
    ZoneFactory.create(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen')
    ZoneFactory.create(slug='hallway', name='Прихожая', thermostat__slug='thermostat-hallway')
    DeviceFactory.create(slug='thermostat-spare')
    DeviceFactory.create(slug='plug-boiler', kind=DeviceKind.PLUG, driver=DeviceDriver.TUYA_PLUG)
    store = ThermostatStateStore(unavailable_after=timedelta(minutes=10))
    store.record_state('thermostat-kitchen', _state(NOW - timedelta(minutes=1)))
    store.record_state('thermostat-hallway', _state(NOW - timedelta(minutes=11)))
    return ThermostatSource(devices=DeviceRepository(), zones=ZoneRepository(), store=store)


def test_thermostats_are_watched_under_their_zone_names(f_source: ThermostatSource) -> None:
    assert f_source.devices(NOW) == [
        MonitoredDevice(
            slug='thermostat-hallway',
            name='Прихожая',
            kind=DeviceKind.THERMOSTAT,
            available=False,
            last_seen_at=NOW - timedelta(minutes=11),
        ),
        MonitoredDevice(
            slug='thermostat-kitchen',
            name='Кухня',
            kind=DeviceKind.THERMOSTAT,
            available=True,
            last_seen_at=NOW - timedelta(minutes=1),
        ),
        MonitoredDevice(
            slug='thermostat-spare',
            name='thermostat-spare',
            kind=DeviceKind.THERMOSTAT,
            available=False,
            last_seen_at=None,
        ),
    ]
