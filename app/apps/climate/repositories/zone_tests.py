from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import AirTargets, ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.domain.zone_config import ZoneConfig, ZoneConfigRecord
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.house.factories import RoomFactory

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
KITCHEN_CONFIG = ZoneConfig(
    name='Кухня',
    heating_enabled=True,
    comfort_floor_setpoint=Decimal('28.0'),
    night_floor_setpoint=Decimal('22.0'),
    boost_floor_setpoint=Decimal('30.0'),
    idle_floor_setpoint=Decimal('18.0'),
    floor_limit_setpoint=Decimal('30.0'),
    comfort_air_target=Decimal('22.0'),
    night_air_target=Decimal('19.5'),
    preheat_lead_minutes=180,
)


@pytest.fixture
def f_zone() -> Zone:
    zone = ZoneFactory.create(slug='kitchen', name='Kitchen', thermostat__slug='thermostat-kitchen')
    ComfortPeriodFactory(zone=zone, weekday=0, start=time(6, 30), end=time(23, 30))
    return zone


@pytest.fixture
def f_air_zones() -> list[Zone]:
    sensor = DeviceFactory.create(
        slug='air-living-room',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        driver=DeviceDriver.ESPHOME_TEMPERATURE,
    )
    return [
        ZoneFactory.create(slug='living-room', air_sensor=sensor),
        ZoneFactory.create(slug='bathroom-1f'),
    ]


@pytest.fixture
def f_air_controlled_zone() -> Zone:
    return ZoneFactory.create(
        slug='living-room',
        air_control_live=True,
        comfort_air_target=Decimal('21.5'),
        night_air_target=Decimal('19.0'),
        idle_floor_setpoint=Decimal('17.0'),
        floor_limit_setpoint=Decimal('29.0'),
        last_air_dry_run_at=NOW,
    )


@pytest.fixture
def f_live_zone() -> Zone:
    return ZoneFactory.create(
        slug='kitchen',
        name='Кухня',
        kind=ZoneKind.AIR,
        live_control=True,
        air_control_live=True,
        boost_until=NOW + timedelta(hours=1),
        last_command_at=NOW - timedelta(minutes=5),
        last_dry_run_at=NOW - timedelta(minutes=6),
        last_air_dry_run_at=NOW - timedelta(minutes=7),
    )


@pytest.fixture
def floor_zone() -> Zone:
    return ZoneFactory.create(slug='bathroom-1f', name='Санузел 1 этаж', kind=ZoneKind.FLOOR, heating_enabled=False)


@pytest.fixture
def placed_zones() -> None:
    open_space = RoomFactory.create(slug='open-space')
    ZoneFactory.create(slug='kitchen', room=open_space)
    ZoneFactory.create(slug='hallway', room=open_space)
    ZoneFactory.create(slug='bathroom-2f')


@pytest.fixture
def f_commanded_zone(f_zone: Zone) -> Zone:
    Zone.objects.filter(pk=f_zone.pk).update(
        last_command_at=NOW - timedelta(minutes=5),
        last_dry_run_at=NOW - timedelta(minutes=5),
        last_air_dry_run_at=NOW - timedelta(minutes=5),
    )
    return f_zone


def test_all_settings_maps_zone(f_zone: Zone) -> None:
    assert ZoneRepository.all_settings() == [
        ZoneSettings(
            slug=f_zone.slug,
            name='Kitchen',
            kind=ZoneKind.AIR,
            thermostat_slug='thermostat-kitchen',
            air_sensor_slug=None,
            heating_enabled=True,
            live_control=False,
            air_control_live=False,
            setpoints=FloorSetpoints(
                comfort=Decimal('28.0'),
                night=Decimal('22.0'),
                boost=Decimal('30.0'),
                idle=Decimal('18.0'),
                limit=Decimal('30.0'),
            ),
            air_targets=AirTargets(comfort=Decimal('22.0'), night=Decimal('19.5')),
            preheat_lead=timedelta(minutes=180),
            windows=(ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30)),),
            boost_until=None,
            last_command_at=None,
            last_dry_run_at=None,
            last_air_dry_run_at=None,
        ),
    ]


def test_all_settings_names_the_air_sensor_of_a_zone_that_has_one(f_air_zones: list[Zone]) -> None:
    assert [(zone.slug, zone.air_sensor_slug) for zone in ZoneRepository.all_settings()] == [
        (f_air_zones[1].slug, None),
        (f_air_zones[0].slug, 'air-living-room'),
    ]


@pytest.mark.usefixtures('placed_zones')
def test_all_settings_name_the_room_of_each_zone() -> None:
    assert [(zone.slug, zone.room_slug) for zone in ZoneRepository.all_settings()] == [
        ('bathroom-2f', None),
        ('hallway', 'open-space'),
        ('kitchen', 'open-space'),
    ]


def test_all_settings_maps_air_control(f_air_controlled_zone: Zone) -> None:
    [zone] = ZoneRepository.all_settings()

    assert (zone.slug, zone.air_control_live, zone.air_targets, zone.setpoints, zone.last_air_dry_run_at) == (
        f_air_controlled_zone.slug,
        True,
        AirTargets(comfort=Decimal('21.5'), night=Decimal('19.0')),
        FloorSetpoints(
            comfort=Decimal('28.0'),
            night=Decimal('22.0'),
            boost=Decimal('30.0'),
            idle=Decimal('17.0'),
            limit=Decimal('29.0'),
        ),
        NOW,
    )


def test_mark_commanded_stores_time(f_zone: Zone) -> None:
    ZoneRepository.mark_commanded(f_zone.slug, NOW)

    assert Zone.objects.get(pk=f_zone.pk).last_command_at == NOW


def test_mark_dry_run_stores_time(f_zone: Zone) -> None:
    ZoneRepository.mark_dry_run(f_zone.slug, NOW)

    zone = Zone.objects.get(pk=f_zone.pk)
    assert (zone.last_dry_run_at, zone.last_command_at) == (NOW, None)


def test_mark_air_dry_run_stores_time_apart_from_write_stamps(f_zone: Zone) -> None:
    ZoneRepository.mark_air_dry_run(f_zone.slug, NOW)

    zone = Zone.objects.get(pk=f_zone.pk)
    assert (zone.last_air_dry_run_at, zone.last_dry_run_at, zone.last_command_at) == (NOW, None, None)


def test_boost_sets_until_and_clears_write_stamps(f_commanded_zone: Zone) -> None:
    ZoneRepository.boost(f_commanded_zone.slug, NOW + timedelta(hours=2), clear_stamps=True)

    zone = Zone.objects.get(pk=f_commanded_zone.pk)
    assert (zone.boost_until, zone.last_command_at, zone.last_dry_run_at, zone.last_air_dry_run_at) == (
        NOW + timedelta(hours=2),
        None,
        None,
        None,
    )


def test_boost_asked_to_keep_the_stamps_sets_until_only(f_commanded_zone: Zone) -> None:
    ZoneRepository.boost(f_commanded_zone.slug, NOW + timedelta(hours=2), clear_stamps=False)

    zone = Zone.objects.get(pk=f_commanded_zone.pk)
    assert (zone.boost_until, zone.last_command_at, zone.last_dry_run_at, zone.last_air_dry_run_at) == (
        NOW + timedelta(hours=2),
        NOW - timedelta(minutes=5),
        NOW - timedelta(minutes=5),
        NOW - timedelta(minutes=5),
    )


def test_boost_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneRepository.boost('attic', NOW, clear_stamps=True)


def test_comfort_period_must_end_after_start(f_zone: Zone) -> None:
    with pytest.raises(IntegrityError):
        ComfortPeriodFactory(zone=f_zone, weekday=1, start=time(23, 0), end=time(6, 0))


@pytest.mark.usefixtures('floor_zone')
def test_config_records_carry_the_settings_and_the_gates(f_live_zone: Zone) -> None:
    assert [record for record in ZoneRepository.config_records() if record.slug == 'kitchen'] == [
        ZoneConfigRecord(
            id=f_live_zone.pk,
            slug='kitchen',
            kind=ZoneKind.AIR,
            config=KITCHEN_CONFIG,
            live_control=True,
            air_control_live=True,
        ),
    ]


@pytest.mark.usefixtures('f_live_zone', 'floor_zone')
def test_config_records_list_every_zone_by_slug() -> None:
    assert [
        (record.slug, record.kind, record.config.heating_enabled) for record in ZoneRepository.config_records()
    ] == [
        ('bathroom-1f', ZoneKind.FLOOR, False),
        ('kitchen', ZoneKind.AIR, True),
    ]


@pytest.mark.usefixtures('f_live_zone')
def test_config_for_update_takes_the_write_lock_on_the_zone_before_it_reads_its_settings() -> None:
    with CaptureQueriesContext(connection) as queries:
        config = ZoneRepository.config_for_update('kitchen')

    assert (config, [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]) == (
        KITCHEN_CONFIG,
        ['UPDATE', 'SELECT'],
    )


def test_config_for_update_of_an_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneRepository.config_for_update('attic')


@pytest.mark.usefixtures('f_live_zone')
@pytest.mark.parametrize('idle', [Decimal('30.0'), Decimal('31.0')], ids=['at_the_floor_limit', 'above_it'])
def test_database_refuses_an_idle_floor_setpoint_not_below_the_floor_limit(idle: Decimal) -> None:
    with pytest.raises(IntegrityError):
        Zone.objects.filter(slug='kitchen').update(idle_floor_setpoint=idle)


def test_saved_config_writes_the_named_fields_only(f_live_zone: Zone) -> None:
    config = replace(
        KITCHEN_CONFIG,
        name='Кухня-столовая',
        comfort_floor_setpoint=Decimal('27.5'),
        night_floor_setpoint=Decimal('21.0'),
    )

    ZoneRepository.save_config('kitchen', config, ('name', 'comfort_floor_setpoint'))

    zone = Zone.objects.get(pk=f_live_zone.pk)
    assert (zone.name, zone.comfort_floor_setpoint, zone.night_floor_setpoint) == (
        'Кухня-столовая',
        Decimal('27.5'),
        Decimal('22.0'),
    )


def test_saved_config_keeps_the_gates_the_boost_and_the_write_stamps(f_live_zone: Zone) -> None:
    ZoneRepository.save_config('kitchen', replace(KITCHEN_CONFIG, heating_enabled=False), ('heating_enabled',))

    zone = Zone.objects.get(pk=f_live_zone.pk)
    assert (
        zone.heating_enabled,
        zone.live_control,
        zone.air_control_live,
        zone.boost_until,
        zone.last_command_at,
        zone.last_dry_run_at,
        zone.last_air_dry_run_at,
    ) == (
        False,
        True,
        True,
        NOW + timedelta(hours=1),
        NOW - timedelta(minutes=5),
        NOW - timedelta(minutes=6),
        NOW - timedelta(minutes=7),
    )


@pytest.mark.usefixtures('f_live_zone')
@pytest.mark.parametrize('gate', ['live_control', 'air_control_live'])
def test_saved_config_refuses_to_write_a_gate(gate: str) -> None:
    with pytest.raises(KeyError):
        ZoneRepository.save_config('kitchen', KITCHEN_CONFIG, (gate,))


def test_saving_the_config_of_an_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneRepository.save_config('attic', KITCHEN_CONFIG, ('name',))
