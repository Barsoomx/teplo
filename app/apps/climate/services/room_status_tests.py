from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.drive import PendingKind, PendingWrite
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.room_intent import IntentReason, RoomIntent
from apps.climate.domain.room_status import RoomStatus, ZoneStatus
from apps.climate.domain.zone_intent import ForcedTarget
from apps.climate.factories import ComfortPeriodFactory, RoomClimateFactory, ZoneFactory
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.services.control_book import ControlBook
from apps.climate.services.room_intents import RoomIntentReader
from apps.climate.services.room_status import RoomStatusReader
from apps.climate.stubs import LoadGateStub
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.domain.readings import TemperatureReading, ThermostatMode, ThermostatState
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.devices.services.state_store import TemperatureStateStore, ThermostatStateStore
from apps.house.factories import FloorFactory, RoomFactory

pytestmark = pytest.mark.django_db

MOSCOW = ZoneInfo('Europe/Moscow')
NOW = datetime(2026, 11, 4, 16, 40, tzinfo=MOSCOW)
OVERRIDE_ENDS = NOW + timedelta(hours=1)
COMFORT_ENDS = datetime(2026, 11, 4, 23, 30, tzinfo=MOSCOW)
THERMOSTATS = (
    ('thermostat-living-room', True, '30.0', '27.5'),
    ('thermostat-kitchen', True, '28.0', '26.0'),
    ('thermostat-hallway', False, '28.0', '24.5'),
    ('thermostat-bathroom-1f', True, '28.0', '25.0'),
)
AIR = (('air-living-room', '21.4'), ('air-kitchen', '21.9'), ('air-second-floor', '18.8'))


def _reader(
    thermostats: ThermostatStateStore,
    temperatures: TemperatureStateStore,
    *,
    dry_run: bool,
) -> RoomStatusReader:
    return RoomStatusReader(
        zones=ZoneRepository(),
        intents=RoomIntentReader(
            rooms=RoomClimateRepository(),
            guests=ClimateSettingsRepository(),
            fireplaces=HeatSourceRepository(),
            book=ControlBook(),
        ),
        temperatures=temperatures,
        thermostats=thermostats,
        gate=LoadGateStub(held=frozenset({'hallway'}), gated=frozenset(), answers={}),
        dry_run=dry_run,
    )


def _sensor(slug: str) -> Device:
    return DeviceFactory.create(slug=slug, kind=DeviceKind.TEMPERATURE_SENSOR, driver=DeviceDriver.ESPHOME_TEMPERATURE)


@pytest.fixture
def house() -> None:
    ground = FloorFactory.create(slug='floor-1', level=1)
    upstairs = FloorFactory.create(slug='floor-2', level=2)
    bathroom_2f = RoomClimateFactory.create(
        room=RoomFactory.create(slug='bathroom-2f', name='Санузел 2 этаж', floor=upstairs, position=3),
    )
    open_space = RoomClimateFactory.create(
        room=RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая', floor=ground, position=1),
        override_target=Decimal('23.0'),
        override_until=OVERRIDE_ENDS,
        override_source='telegram',
    )
    bathroom_1f = RoomClimateFactory.create(
        room=RoomFactory.create(slug='bathroom-1f', name='Санузел 1 этаж', floor=ground, position=2),
    )
    RoomClimateFactory.create(
        room=RoomFactory.create(slug='bedroom', name='Спальня', floor=upstairs, position=1),
        air_sensor=_sensor('air-second-floor'),
    )
    for climate in (open_space, bathroom_1f):
        ComfortPeriodFactory.create_batch(7, zone=None, room_climate=climate)
    ZoneFactory.create(
        slug='living-room',
        name='Гостиная',
        thermostat__slug='thermostat-living-room',
        air_sensor=_sensor('air-living-room'),
        room=open_space.room,
        live_control=True,
        air_control_live=True,
    )
    ZoneFactory.create(
        slug='kitchen',
        name='Кухня',
        thermostat__slug='thermostat-kitchen',
        air_sensor=_sensor('air-kitchen'),
        room=open_space.room,
        live_control=True,
        last_command_at=NOW - timedelta(minutes=18),
    )
    for slug, name, climate in (
        ('hallway', 'Прихожая', open_space),
        ('bathroom-1f', 'Санузел 1 этаж', bathroom_1f),
        ('bathroom-2f', 'Санузел 2 этаж', bathroom_2f),
    ):
        ZoneFactory.create(
            slug=slug,
            name=name,
            kind=ZoneKind.FLOOR,
            thermostat__slug=f'thermostat-{slug}',
            room=climate.room,
            live_control=True,
        )


@pytest.fixture
def thermostats() -> ThermostatStateStore:
    store = ThermostatStateStore(unavailable_after=timedelta(hours=12))
    for slug, power, setpoint, floor in THERMOSTATS:
        store.record_state(
            slug,
            ThermostatState(
                power=power,
                mode=ThermostatMode.HOLD,
                setpoint=Decimal(setpoint),
                floor_temperature=Decimal(floor),
                heating=None,
                read_at=NOW,
            ),
        )
    return store


@pytest.fixture
def temperatures() -> TemperatureStateStore:
    store = TemperatureStateStore(unavailable_after=timedelta(hours=12))
    for slug, temperature in AIR:
        store.record_state(slug, TemperatureReading(temperature=Decimal(temperature), humidity=None, read_at=NOW))
    return store


@pytest.fixture
def f_reader(thermostats: ThermostatStateStore, temperatures: TemperatureStateStore) -> RoomStatusReader:
    return _reader(thermostats, temperatures, dry_run=False)


@pytest.fixture
def f_dry_run_reader(thermostats: ThermostatStateStore, temperatures: TemperatureStateStore) -> RoomStatusReader:
    return _reader(thermostats, temperatures, dry_run=True)


@pytest.mark.usefixtures('house')
def test_rooms_give_each_room_s_target_and_what_each_floor_takes_and_when(f_reader: RoomStatusReader) -> None:
    rooms = f_reader.rooms(NOW, clock_trusted=True)

    held = PendingWrite(kind=PendingKind.HELD, at=None)
    assert rooms == [
        RoomStatus(
            slug='open-space',
            name='Гостиная, кухня и прихожая',
            air=None,
            intent=RoomIntent(
                room='open-space',
                reason=IntentReason.OVERRIDE,
                target=Decimal('23.0'),
                until=OVERRIDE_ENDS,
                forced=(ForcedTarget(target=Decimal('23.0'), beneath=Decimal('22.0')),),
            ),
            adjustable=True,
            guest_room=False,
            zones=(
                ZoneStatus(
                    slug='living-room',
                    name='Гостиная',
                    available=True,
                    air=Decimal('21.4'),
                    floor_temperature=Decimal('27.5'),
                    setpoint=Decimal('30.0'),
                    power=True,
                    air_path=True,
                    floor_target=None,
                    pending=None,
                ),
                ZoneStatus(
                    slug='kitchen',
                    name='Кухня',
                    available=True,
                    air=Decimal('21.9'),
                    floor_temperature=Decimal('26.0'),
                    setpoint=Decimal('28.0'),
                    power=True,
                    air_path=False,
                    floor_target=Decimal('30.0'),
                    pending=PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW + timedelta(minutes=12)),
                ),
                ZoneStatus(
                    slug='hallway',
                    name='Прихожая',
                    available=True,
                    air=None,
                    floor_temperature=Decimal('24.5'),
                    setpoint=Decimal('28.0'),
                    power=False,
                    air_path=False,
                    floor_target=Decimal('30.0'),
                    pending=held,
                ),
            ),
        ),
        RoomStatus(
            slug='bathroom-1f',
            name='Санузел 1 этаж',
            air=None,
            intent=RoomIntent(
                room='bathroom-1f',
                reason=IntentReason.COMFORT,
                target=Decimal('22.0'),
                until=COMFORT_ENDS,
                forced=(),
            ),
            adjustable=False,
            guest_room=False,
            zones=(
                ZoneStatus(
                    slug='bathroom-1f',
                    name='Санузел 1 этаж',
                    available=True,
                    air=None,
                    floor_temperature=Decimal('25.0'),
                    setpoint=Decimal('28.0'),
                    power=True,
                    air_path=False,
                    floor_target=Decimal('28.0'),
                    pending=PendingWrite(kind=PendingKind.HOLDS, at=None),
                ),
            ),
        ),
        RoomStatus(
            slug='bedroom',
            name='Спальня',
            air=Decimal('18.8'),
            intent=RoomIntent(
                room='bedroom',
                reason=IntentReason.SETBACK,
                target=Decimal('19.5'),
                until=None,
                forced=(),
            ),
            adjustable=False,
            guest_room=False,
            zones=(),
        ),
        RoomStatus(
            slug='bathroom-2f',
            name='Санузел 2 этаж',
            air=None,
            intent=RoomIntent(
                room='bathroom-2f',
                reason=IntentReason.SETBACK,
                target=Decimal('19.5'),
                until=None,
                forced=(),
            ),
            adjustable=False,
            guest_room=False,
            zones=(
                ZoneStatus(
                    slug='bathroom-2f',
                    name='Санузел 2 этаж',
                    available=False,
                    air=None,
                    floor_temperature=None,
                    setpoint=None,
                    power=None,
                    air_path=False,
                    floor_target=Decimal('22.0'),
                    pending=PendingWrite(kind=PendingKind.UNREACHED, at=None),
                ),
            ),
        ),
    ]


@pytest.mark.usefixtures('house')
def test_a_floor_teplo_only_watches_takes_no_write(f_dry_run_reader: RoomStatusReader) -> None:
    rooms = f_dry_run_reader.rooms(NOW, clock_trusted=True)

    assert [
        (zone.slug, None if zone.pending is None else zone.pending.kind) for room in rooms for zone in room.zones
    ] == [
        ('living-room', None),
        ('kitchen', PendingKind.WATCHING),
        ('hallway', PendingKind.HELD),
        ('bathroom-1f', PendingKind.HOLDS),
        ('bathroom-2f', PendingKind.UNREACHED),
    ]
