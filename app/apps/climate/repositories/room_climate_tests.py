from dataclasses import replace
from datetime import UTC, datetime, time
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.backup_heat import BackupHeat
from apps.climate.domain.heat_sources import HeatSourceKind, SourceGate
from apps.climate.domain.modes import CommandSource, ZoneKind
from apps.climate.domain.room_climate import (
    RoomClimateConfig,
    RoomClimateConfigRecord,
    RoomClimateSettings,
    RoomOverride,
)
from apps.climate.domain.zone import AirTargets, ComfortWindow
from apps.climate.factories import ComfortPeriodFactory, HeatSourceFactory, RoomClimateFactory, ZoneFactory
from apps.climate.models import HeatSource, RoomClimate
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.house.domain.errors import RoomNotFoundError
from apps.house.factories import FloorFactory, RoomFactory

pytestmark = pytest.mark.django_db

BEDROOM_CONFIG = RoomClimateConfig(
    comfort_target=Decimal('20.5'),
    setback_target=Decimal('19.5'),
    backup_below=Decimal('19.0'),
    backup_hysteresis=Decimal('0.30'),
    guest_room=False,
)
UNTIL = datetime(2026, 11, 4, 15, 40, tzinfo=UTC)
OVERRIDE = RoomOverride(target=Decimal('22.5'), until=UNTIL, source=CommandSource.TELEGRAM)


@pytest.fixture
def f_bedroom() -> RoomClimate:
    return RoomClimateFactory.create(
        room=RoomFactory.create(
            slug='bedroom',
            name='Спальня',
            position=1,
            floor=FloorFactory.create(name='2 этаж', level=2),
        ),
        air_sensor=DeviceFactory.create(
            slug='air-second-floor',
            kind=DeviceKind.TEMPERATURE_SENSOR,
            driver=DeviceDriver.ESPHOME_TEMPERATURE,
        ),
        comfort_target=Decimal('20.5'),
        backup_zone=ZoneFactory.create(slug='bathroom-2f', name='Санузел 2 этаж'),
        backup_live=True,
    )


@pytest.fixture
def f_open_space() -> RoomClimate:
    climate = RoomClimateFactory.create(
        room=RoomFactory.create(
            slug='open-space',
            name='Гостиная, кухня и прихожая',
            floor=FloorFactory.create(name='1 этаж', level=1),
        ),
        setback_target=Decimal('19.0'),
        guest_room=True,
    )
    ComfortPeriodFactory.create(zone=None, room_climate=climate, weekday=1, start=time(7, 0), end=time(22, 0))
    ComfortPeriodFactory.create(zone=None, room_climate=climate, weekday=0, start=time(6, 30), end=time(23, 30))
    return climate


@pytest.mark.usefixtures('f_bedroom', 'f_open_space')
def test_all_settings_read_targets_windows_air_sensor_and_backup_in_floor_order() -> None:
    assert RoomClimateRepository.all_settings() == [
        RoomClimateSettings(
            slug='open-space',
            name='Гостиная, кухня и прихожая',
            air_sensor_slug=None,
            targets=AirTargets(comfort=Decimal('22.0'), night=Decimal('19.0')),
            windows=(
                ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30)),
                ComfortWindow(weekday=1, start=time(7, 0), end=time(22, 0)),
            ),
            backup=BackupHeat(zone_slug=None, below=Decimal('19.0'), hysteresis=Decimal('0.30'), live=False),
            guest_room=True,
            override=None,
            adjustable=False,
        ),
        RoomClimateSettings(
            slug='bedroom',
            name='Спальня',
            air_sensor_slug='air-second-floor',
            targets=AirTargets(comfort=Decimal('20.5'), night=Decimal('19.5')),
            windows=(),
            backup=BackupHeat(zone_slug='bathroom-2f', below=Decimal('19.0'), hysteresis=Decimal('0.30'), live=True),
            guest_room=False,
            override=None,
            adjustable=False,
        ),
    ]


@pytest.fixture
def f_zoned_open_space(request: pytest.FixtureRequest, f_open_space: RoomClimate) -> RoomClimate:
    for slug, kind, sensor in request.param:
        ZoneFactory.create(
            slug=slug,
            kind=kind,
            room=f_open_space.room,
            air_sensor=None
            if sensor is None
            else DeviceFactory.create(
                slug=sensor,
                kind=DeviceKind.TEMPERATURE_SENSOR,
                driver=DeviceDriver.ESPHOME_TEMPERATURE,
            ),
        )
    return f_open_space


@pytest.fixture
def f_overridden_open_space(f_open_space: RoomClimate) -> RoomClimate:
    RoomClimateRepository.save_override('open-space', OVERRIDE)
    return f_open_space


@pytest.mark.usefixtures('f_bedroom')
@pytest.mark.parametrize(
    ('f_zoned_open_space', 'adjustable'),
    [
        ((('kitchen', ZoneKind.AIR, 'air-kitchen'),), True),
        ((('kitchen', ZoneKind.AIR, 'air-kitchen'), ('hallway', ZoneKind.AIR, 'air-hallway')), True),
        ((('kitchen', ZoneKind.AIR, None),), False),
        ((('bathroom-1f', ZoneKind.FLOOR, None),), False),
        ((), False),
    ],
    ids=['an_air_zone_with_its_sensor', 'two_of_them', 'air_zone_without_a_sensor', 'floor_zone', 'no_zone'],
    indirect=['f_zoned_open_space'],
)
def test_a_room_is_adjustable_while_one_of_its_zones_reads_the_air(
    f_zoned_open_space: RoomClimate,
    adjustable: bool,
) -> None:
    assert [(room.slug, room.adjustable) for room in RoomClimateRepository.all_settings()] == [
        (f_zoned_open_space.room.slug, adjustable),
        ('bedroom', False),
    ]


@pytest.mark.usefixtures('f_bedroom', 'f_overridden_open_space')
def test_override_saved_for_a_room_is_read_back_with_its_source() -> None:
    assert [(room.slug, room.override) for room in RoomClimateRepository.all_settings()] == [
        ('open-space', OVERRIDE),
        ('bedroom', None),
    ]


@pytest.mark.usefixtures('f_bedroom')
def test_cleared_override_leaves_no_target_end_or_source(f_overridden_open_space: RoomClimate) -> None:
    RoomClimateRepository.save_override('open-space', None)

    stored = RoomClimate.objects.get(pk=f_overridden_open_space.pk)
    assert (
        (stored.override_target, stored.override_until, stored.override_source),
        RoomClimateRepository.all_settings()[0].override,
    ) == ((None, None, ''), None)


def test_saving_an_override_of_an_unknown_room_raises() -> None:
    with pytest.raises(RoomNotFoundError):
        RoomClimateRepository.save_override('attic', OVERRIDE)


@pytest.mark.usefixtures('f_bedroom', 'f_overridden_open_space')
def test_comfort_for_update_takes_the_write_lock_before_it_reads_the_room() -> None:
    with CaptureQueriesContext(connection) as queries:
        room = RoomClimateRepository.comfort_for_update('open-space')

    assert (
        (room.slug, room.guest_room, room.override, room.adjustable, len(room.windows)),
        [query['sql'].split(' ', 1)[0] for query in queries.captured_queries][:2],
    ) == (('open-space', True, OVERRIDE, False, 2), ['UPDATE', 'SELECT'])


def test_comfort_for_update_of_a_room_without_a_room_climate_raises() -> None:
    RoomFactory.create(slug='office')

    with pytest.raises(RoomNotFoundError):
        RoomClimateRepository.comfort_for_update('office')


@pytest.mark.usefixtures('f_open_space')
def test_config_records_name_the_room_its_floor_and_its_backup_zone(f_bedroom: RoomClimate) -> None:
    assert RoomClimateRepository.config_records()[1:] == [
        RoomClimateConfigRecord(
            id=f_bedroom.pk,
            slug='bedroom',
            name='Спальня',
            floor_name='2 этаж',
            config=BEDROOM_CONFIG,
            air_sensor_slug='air-second-floor',
            backup_zone_slug='bathroom-2f',
            backup_zone_name='Санузел 2 этаж',
            backup_live=True,
        ),
    ]


@pytest.fixture
def f_fireplace(f_open_space: RoomClimate) -> HeatSource:
    return HeatSourceFactory.create(slug='fireplace', name='Камин', room=f_open_space.room, live=True)


@pytest.mark.usefixtures('f_bedroom')
def test_config_records_give_each_room_the_gates_of_its_heat_sources(f_fireplace: HeatSource) -> None:
    assert [(record.slug, record.sources) for record in RoomClimateRepository.config_records()] == [
        ('open-space', (SourceGate(id=f_fireplace.pk, name='Камин', kind=HeatSourceKind.FIREPLACE, live=True),)),
        ('bedroom', ()),
    ]


@pytest.mark.usefixtures('f_bedroom')
def test_config_for_update_takes_the_write_lock_on_the_room_climate_before_it_reads_it() -> None:
    with CaptureQueriesContext(connection) as queries:
        record = RoomClimateRepository.config_for_update('bedroom')

    assert (record.config, [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]) == (
        BEDROOM_CONFIG,
        ['UPDATE', 'SELECT', 'SELECT'],
    )


def test_config_for_update_of_an_unknown_room_raises() -> None:
    RoomFactory.create(slug='office')

    with pytest.raises(RoomNotFoundError):
        RoomClimateRepository.config_for_update('office')


@pytest.mark.usefixtures('f_bedroom')
def test_config_record_reads_and_save_config_writes_the_guest_room() -> None:
    RoomClimateRepository.save_config('bedroom', replace(BEDROOM_CONFIG, guest_room=True), ('guest_room',))

    assert (
        RoomClimateRepository.config_for_update('bedroom').config.guest_room,
        RoomClimate.objects.get(room__slug='bedroom').guest_room,
    ) == (True, True)


def test_save_config_writes_only_the_named_fields(f_bedroom: RoomClimate) -> None:
    config = replace(BEDROOM_CONFIG, comfort_target=Decimal('21.0'), backup_below=Decimal('18.0'))

    RoomClimateRepository.save_config('bedroom', config, ('comfort_target',))

    stored = RoomClimate.objects.get(pk=f_bedroom.pk)
    assert (stored.comfort_target, stored.backup_below, stored.backup_live, stored.backup_zone_id) == (
        Decimal('21.0'),
        Decimal('19.0'),
        True,
        f_bedroom.backup_zone_id,
    )


def test_save_config_of_an_unknown_room_raises() -> None:
    with pytest.raises(RoomNotFoundError):
        RoomClimateRepository.save_config('attic', BEDROOM_CONFIG, ('comfort_target',))


@pytest.mark.usefixtures('f_bedroom')
def test_save_config_refuses_to_write_the_gate() -> None:
    with pytest.raises(KeyError):
        RoomClimateRepository.save_config('bedroom', BEDROOM_CONFIG, ('backup_live',))


@pytest.mark.usefixtures('f_bedroom')
@pytest.mark.parametrize('setback', [Decimal('20.6'), Decimal('21.0')], ids=['just_above', 'well_above'])
def test_database_refuses_a_night_target_above_comfort(setback: Decimal) -> None:
    with pytest.raises(IntegrityError):
        RoomClimate.objects.filter(room__slug='bedroom').update(setback_target=setback, backup_below=Decimal('18.0'))


@pytest.mark.usefixtures('f_bedroom')
@pytest.mark.parametrize(
    'below', [Decimal('19.1'), Decimal('19.5')], ids=['within_half_a_degree', 'at_the_night_target']
)
def test_database_refuses_backup_heat_closer_than_half_a_degree_below_the_night_target(below: Decimal) -> None:
    with pytest.raises(IntegrityError):
        RoomClimate.objects.filter(room__slug='bedroom').update(backup_below=below)


@pytest.mark.usefixtures('f_bedroom')
@pytest.mark.parametrize(
    ('setback', 'below'),
    [(Decimal('19.5'), Decimal('19.0')), (Decimal('16.4'), Decimal('15.9')), (Decimal('8.4'), Decimal('7.9'))],
    ids=['as_seeded', 'across_sixteen_degrees', 'across_eight_degrees'],
)
def test_database_takes_backup_heat_exactly_half_a_degree_below_the_night_target(
    setback: Decimal,
    below: Decimal,
) -> None:
    RoomClimate.objects.filter(room__slug='bedroom').update(setback_target=setback, backup_below=below)

    stored = RoomClimate.objects.get(room__slug='bedroom')
    assert (stored.setback_target, stored.backup_below) == (setback, below)


def test_database_lets_a_room_without_a_backup_zone_keep_any_backup_threshold(f_open_space: RoomClimate) -> None:
    RoomClimate.objects.filter(pk=f_open_space.pk).update(backup_below=Decimal('25.0'))

    assert RoomClimate.objects.get(pk=f_open_space.pk).backup_below == Decimal('25.0')
