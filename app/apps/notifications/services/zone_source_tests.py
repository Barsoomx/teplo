from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.factories import ClimateSettingsFactory, ComfortPeriodFactory, RoomClimateFactory, ZoneFactory
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.services.control_book import ControlBook
from apps.climate.services.room_intents import RoomIntentReader
from apps.devices.domain.readings import ThermostatMode, ThermostatState
from apps.devices.services.state_store import ThermostatStateStore
from apps.house.factories import RoomFactory
from apps.notifications.domain.alerts import ZoneHeating
from apps.notifications.services.zone_source import ZoneHeatingSource

pytestmark = pytest.mark.django_db

EVENING = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)
NIGHT = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)
ZONES = [
    ZoneHeating(slug='hallway', name='Прихожая', should_heat=False, power=False),
    ZoneHeating(slug='kitchen', name='Кухня', should_heat=True, power=False),
    ZoneHeating(slug='living-room', name='Гостиная', should_heat=True, power=None),
]


def _state(read_at: datetime) -> ThermostatState:
    return ThermostatState(
        power=False,
        mode=ThermostatMode.HOLD,
        setpoint=Decimal('22.0'),
        floor_temperature=Decimal('21.5'),
        heating=False,
        read_at=read_at,
    )


def _source() -> ZoneHeatingSource:
    store = ThermostatStateStore(unavailable_after=timedelta(hours=12))
    for slug in ('thermostat-kitchen', 'thermostat-hallway'):
        store.record_state(slug, _state(EVENING - timedelta(minutes=1)))
    return ZoneHeatingSource(
        zones=ZoneRepository(),
        intents=RoomIntentReader(rooms=RoomClimateRepository(), guests=ClimateSettingsRepository(), book=ControlBook()),
        store=store,
    )


@pytest.fixture
def f_source() -> ZoneHeatingSource:
    kitchen = ZoneFactory.create(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen')
    ComfortPeriodFactory.create_batch(7, zone=kitchen)
    ZoneFactory.create(slug='hallway', name='Прихожая', thermostat__slug='thermostat-hallway', heating_enabled=False)
    ZoneFactory.create(slug='living-room', name='Гостиная', thermostat__slug='thermostat-living-room')
    return _source()


@pytest.fixture
def f_intent_source(request: pytest.FixtureRequest) -> ZoneHeatingSource:
    override_target, guests_until = request.param
    climate = RoomClimateFactory.create(
        room=RoomFactory.create(slug='open-space'),
        guest_room=True,
        override_target=override_target,
        override_until=None if override_target is None else NIGHT + timedelta(hours=1),
        override_source='panel',
    )
    ComfortPeriodFactory.create_batch(7, zone=None, room_climate=climate)
    ClimateSettingsFactory.create(guests_until=guests_until)
    for slug, name, enabled in (('kitchen', 'Кухня', True), ('hallway', 'Прихожая', False)):
        ZoneFactory.create(
            slug=slug,
            name=name,
            thermostat__slug=f'thermostat-{slug}',
            heating_enabled=enabled,
            room=climate.room,
        )
    ZoneFactory.create(slug='living-room', name='Гостиная', thermostat__slug='thermostat-living-room')
    return _source()


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

    assert zones == ZONES


@pytest.mark.parametrize(
    'f_intent_source',
    [
        (Decimal('12.0'), None),
        (Decimal('25.0'), None),
        (None, NIGHT + timedelta(hours=12)),
    ],
    ids=['override_below_the_schedule', 'override_above_the_schedule', 'guests'],
    indirect=True,
)
@pytest.mark.parametrize('clock_trusted', [True, False], ids=['trusted_clock', 'untrusted_clock'])
def test_overrides_and_guests_never_switch_a_floor_off_and_a_disabled_zone_stays_off(
    f_intent_source: ZoneHeatingSource,
    clock_trusted: bool,
) -> None:
    zones = f_intent_source.zones(NIGHT, clock_trusted=clock_trusted)

    assert zones == ZONES
