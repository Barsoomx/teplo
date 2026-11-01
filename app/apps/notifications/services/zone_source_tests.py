from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.services.state_store import ThermostatStateStore
from apps.notifications.domain.alerts import ZoneHeating
from apps.notifications.services.zone_source import ZoneHeatingSource

pytestmark = pytest.mark.django_db

EVENING = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)
NIGHT = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)


def _state(read_at: datetime) -> ThermostatState:
    return ThermostatState(
        power=False,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('22.0'),
        floor_temperature=Decimal('21.5'),
        read_at=read_at,
    )


@pytest.fixture
def f_source() -> ZoneHeatingSource:
    kitchen = ZoneFactory.create(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen')
    ComfortPeriodFactory.create_batch(7, zone=kitchen)
    ZoneFactory.create(slug='hallway', name='Прихожая', thermostat__slug='thermostat-hallway', heating_enabled=False)
    ZoneFactory.create(slug='living-room', name='Гостиная', thermostat__slug='thermostat-living-room')
    store = ThermostatStateStore(unavailable_after=timedelta(hours=12))
    for slug in ('thermostat-kitchen', 'thermostat-hallway'):
        store.record_state(slug, _state(EVENING - timedelta(minutes=1)))
    return ZoneHeatingSource(zones=ZoneRepository(), store=store)


@pytest.mark.parametrize(
    ('now', 'clock_trusted'),
    [
        (EVENING, True),
        (NIGHT, True),
        (NIGHT, False),
    ],
)
def test_enabled_zones_should_heat_at_any_hour_and_report_the_power_read(
    f_source: ZoneHeatingSource,
    now: datetime,
    clock_trusted: bool,
) -> None:
    zones = f_source.zones(now, clock_trusted=clock_trusted)

    assert zones == [
        ZoneHeating(slug='hallway', name='Прихожая', should_heat=False, power=False),
        ZoneHeating(slug='kitchen', name='Кухня', should_heat=True, power=False),
        ZoneHeating(slug='living-room', name='Гостиная', should_heat=True, power=None),
    ]
