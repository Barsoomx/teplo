from dataclasses import replace
from datetime import time
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.backup_heat import BackupHeat
from apps.climate.domain.room_climate import RoomClimateConfig, RoomClimateConfigRecord, RoomClimateSettings
from apps.climate.domain.zone import AirTargets, ComfortWindow
from apps.climate.factories import ComfortPeriodFactory, RoomClimateFactory, ZoneFactory
from apps.climate.models import RoomClimate
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
)


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
        ),
        RoomClimateSettings(
            slug='bedroom',
            name='Спальня',
            air_sensor_slug='air-second-floor',
            targets=AirTargets(comfort=Decimal('20.5'), night=Decimal('19.5')),
            windows=(),
            backup=BackupHeat(zone_slug='bathroom-2f', below=Decimal('19.0'), hysteresis=Decimal('0.30'), live=True),
        ),
    ]


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


@pytest.mark.usefixtures('f_bedroom')
def test_config_for_update_takes_the_write_lock_on_the_room_climate_before_it_reads_it() -> None:
    with CaptureQueriesContext(connection) as queries:
        record = RoomClimateRepository.config_for_update('bedroom')

    assert (record.config, [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]) == (
        BEDROOM_CONFIG,
        ['UPDATE', 'SELECT'],
    )


def test_config_for_update_of_an_unknown_room_raises() -> None:
    RoomFactory.create(slug='office')

    with pytest.raises(RoomNotFoundError):
        RoomClimateRepository.config_for_update('office')


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
