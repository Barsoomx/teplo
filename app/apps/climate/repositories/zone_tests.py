from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.control import ClimateControl
from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.learning import ZoneLearningRecord
from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import AirTargets, ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.domain.zone_config import ZoneConfig, ZoneConfigRecord
from apps.climate.domain.zone_cycle import CycleConditions, ZoneConditions, ZoneDecision, decide_zone
from apps.climate.domain.zone_intent import NO_INTENT
from apps.climate.factories import ComfortPeriodFactory, RoomClimateFactory, ZoneFactory
from apps.climate.models import ComfortPeriod, Zone
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.house.factories import RoomFactory
from apps.house.models import Room

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
WEEK_START = datetime(2026, 11, 2, 0, 0, tzinfo=ZoneInfo('Europe/Moscow'))
WEEK_MINUTES = 7 * 1440
AIR_EVERY_MINUTES = 15
OPEN_SPACE_LEADS = (('living-room', 180), ('kitchen', 150), ('hallway', 240))
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
def f_zone_in_a_room_climate() -> Zone:
    climate = RoomClimateFactory.create(
        room=RoomFactory.create(slug='open-space'),
        comfort_target=Decimal('21.5'),
        setback_target=Decimal('19.0'),
    )
    zone = ZoneFactory.create(slug='kitchen', room=climate.room)
    ComfortPeriodFactory.create(zone=zone, weekday=0, start=time(6, 30), end=time(23, 30))
    ComfortPeriodFactory.create(zone=None, room_climate=climate, weekday=0, start=time(7, 0), end=time(22, 0))
    ComfortPeriodFactory.create(zone=None, room_climate=climate, weekday=6, start=time(9, 0), end=time(23, 0))
    return zone


@pytest.fixture
def zones_without_a_room_climate() -> list[Zone]:
    kitchen = ZoneFactory.create(
        slug='kitchen',
        room=RoomFactory.create(slug='open-space'),
        comfort_air_target=Decimal('21.0'),
        night_air_target=Decimal('18.5'),
    )
    attic = ZoneFactory.create(slug='attic')
    ComfortPeriodFactory.create(zone=kitchen, weekday=0, start=time(6, 30), end=time(23, 30))
    ComfortPeriodFactory.create(zone=attic, weekday=3, start=time(12, 0), end=time(13, 0))
    return [kitchen, attic]


@pytest.fixture
def open_space() -> Room:
    room = RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая')
    for slug, lead in OPEN_SPACE_LEADS:
        zone = ZoneFactory.create(
            slug=slug,
            room=room,
            air_sensor=DeviceFactory.create(
                slug=f'air-{slug}',
                kind=DeviceKind.TEMPERATURE_SENSOR,
                driver=DeviceDriver.ESPHOME_TEMPERATURE,
            ),
            comfort_air_target=Decimal('22.0'),
            night_air_target=Decimal('19.5'),
            preheat_lead_minutes=lead,
        )
        for weekday in range(7):
            ComfortPeriodFactory.create(zone=zone, weekday=weekday, start=time(12, 0), end=time(13, 0))
    return room


@pytest.fixture
def f_zone_built(open_space: Room) -> list[ZoneSettings]:
    return [zone for zone in ZoneRepository.all_settings() if zone.room_slug == open_space.slug]


@pytest.fixture
def f_room_built(open_space: Room, f_zone_built: list[ZoneSettings]) -> list[ZoneSettings]:
    climate = RoomClimateFactory.create(
        room=open_space,
        comfort_target=Decimal('22.0'),
        setback_target=Decimal('19.5'),
    )
    ComfortPeriod.objects.filter(zone__slug='living-room').update(room_climate=climate)
    zone_built = {zone.slug for zone in f_zone_built}
    return [zone for zone in ZoneRepository.all_settings() if zone.slug in zone_built]


@pytest.fixture
def f_commanded_zone(f_zone: Zone) -> Zone:
    Zone.objects.filter(pk=f_zone.pk).update(
        last_command_at=NOW - timedelta(minutes=5),
        last_dry_run_at=NOW - timedelta(minutes=5),
        last_air_dry_run_at=NOW - timedelta(minutes=5),
    )
    return f_zone


@pytest.fixture
def f_held_zone(f_commanded_zone: Zone) -> Zone:
    Zone.objects.filter(pk=f_commanded_zone.pk).update(manual_until=NOW + timedelta(hours=1))
    return f_commanded_zone


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


def test_a_zone_takes_windows_and_air_targets_from_its_room_climate(f_zone_in_a_room_climate: Zone) -> None:
    [zone] = ZoneRepository.all_settings()
    [record] = ZoneRepository.config_records()

    assert (
        zone.slug,
        zone.windows,
        zone.air_targets,
        zone.preheat_lead,
        record.room,
        record.config.comfort_air_target,
    ) == (
        f_zone_in_a_room_climate.slug,
        (
            ComfortWindow(weekday=0, start=time(7, 0), end=time(22, 0)),
            ComfortWindow(weekday=6, start=time(9, 0), end=time(23, 0)),
        ),
        AirTargets(comfort=Decimal('21.5'), night=Decimal('19.0')),
        timedelta(minutes=180),
        'open-space',
        Decimal('22.0'),
    )


@pytest.mark.usefixtures('zones_without_a_room_climate')
def test_a_zone_without_a_room_climate_keeps_its_own_comfort() -> None:
    zones = ZoneRepository.all_settings()

    assert (
        [(zone.slug, zone.room_slug, zone.windows, zone.air_targets) for zone in zones],
        [(record.slug, record.room) for record in ZoneRepository.config_records()],
    ) == (
        [
            (
                'attic',
                None,
                (ComfortWindow(weekday=3, start=time(12, 0), end=time(13, 0)),),
                AirTargets(comfort=Decimal('22.0'), night=Decimal('19.5')),
            ),
            (
                'kitchen',
                'open-space',
                (ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30)),),
                AirTargets(comfort=Decimal('21.0'), night=Decimal('18.5')),
            ),
        ],
        [('attic', None), ('kitchen', None)],
    )


def test_zones_decide_every_minute_of_a_week_as_before_once_their_room_holds_the_comfort(
    f_zone_built: list[ZoneSettings],
    f_room_built: list[ZoneSettings],
) -> None:
    control = ClimateSettingsRepository.control()

    decisions = (_week_of_decisions(f_room_built, control), _week_of_decisions(f_zone_built, control))

    assert (f_room_built, len(decisions[0]), decisions[0] == decisions[1]) == (
        f_zone_built,
        len(OPEN_SPACE_LEADS) * (WEEK_MINUTES + WEEK_MINUTES // AIR_EVERY_MINUTES),
        True,
    )


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


@pytest.mark.usefixtures('f_held_zone')
def test_all_settings_read_the_end_of_a_hold_by_hand() -> None:
    assert [zone.manual_until for zone in ZoneRepository.all_settings()] == [NOW + timedelta(hours=1)]


def test_hold_by_hand_stores_its_end_and_leaves_the_stamps(f_commanded_zone: Zone) -> None:
    held = ZoneRepository.hold_by_hand(
        f_commanded_zone.slug,
        NOW + timedelta(hours=2),
        stamped_at=NOW - timedelta(minutes=5),
    )

    zone = Zone.objects.get(pk=f_commanded_zone.pk)
    assert (held, zone.manual_until, zone.last_command_at) == (
        True,
        NOW + timedelta(hours=2),
        NOW - timedelta(minutes=5),
    )


@pytest.mark.parametrize(
    'stamped_at',
    [None, NOW - timedelta(minutes=35)],
    ids=['before_its_first_write', 'before_its_last_write'],
)
def test_hold_by_hand_of_a_zone_teplo_stamped_since_it_was_read_stores_nothing(
    f_commanded_zone: Zone,
    stamped_at: datetime | None,
) -> None:
    held = ZoneRepository.hold_by_hand(f_commanded_zone.slug, NOW + timedelta(hours=2), stamped_at=stamped_at)

    assert (held, Zone.objects.get(pk=f_commanded_zone.pk).manual_until) == (False, None)


def test_hold_by_hand_of_an_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ZoneRepository.hold_by_hand('attic', NOW, stamped_at=None)


def test_mark_shed_stamps_the_write_and_ends_a_hold_by_hand(f_held_zone: Zone) -> None:
    ZoneRepository.mark_shed(f_held_zone.slug, NOW)

    zone = Zone.objects.get(pk=f_held_zone.pk)
    assert (zone.last_command_at, zone.manual_until) == (NOW, None)


@pytest.mark.parametrize('clear_stamps', [True, False], ids=['clearing_the_stamps', 'keeping_the_stamps'])
def test_boost_ends_a_hold_by_hand(f_held_zone: Zone, clear_stamps: bool) -> None:
    ZoneRepository.boost(f_held_zone.slug, NOW + timedelta(hours=2), clear_stamps=clear_stamps)

    assert Zone.objects.get(pk=f_held_zone.pk).manual_until is None


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


def _week_of_decisions(zones: list[ZoneSettings], control: ClimateControl) -> list[ZoneDecision]:
    floor = ZoneConditions(air=None, learning=ZoneLearningRecord.unlearned(), intent=NO_INTENT, writes_live=True)
    air = replace(floor, air=Decimal('20.4'))
    decisions: list[ZoneDecision] = []
    for zone in zones:
        for minute in range(WEEK_MINUTES):
            now = WEEK_START + timedelta(minutes=minute)
            decisions.append(
                decide_zone(
                    zone,
                    ZoneMemory(),
                    floor,
                    CycleConditions(now=now, clock_trusted=True, control=control, outdoor=None),
                ),
            )
            if minute % AIR_EVERY_MINUTES == 0:
                decisions.append(
                    decide_zone(
                        zone,
                        ZoneMemory(),
                        air,
                        CycleConditions(now=now, clock_trusted=True, control=control, outdoor=Decimal('-3.4')),
                    ),
                )
    return decisions
