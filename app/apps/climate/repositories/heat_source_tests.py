from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.errors import HeatSourceNotFoundError
from apps.climate.domain.heat_sources import FireplaceRecord, HeatSourceKind, SourceSettings
from apps.climate.factories import HeatSourceFactory
from apps.climate.models import HeatSource
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.house.factories import FloorFactory, RoomFactory
from apps.house.models import Room
from apps.power.factories import LoadSettingsFactory, SourceLoadFactory

pytestmark = pytest.mark.django_db

LIT_UNTIL = datetime(2026, 11, 4, 19, 10, tzinfo=UTC)
FIREPLACE = FireplaceRecord(
    slug='fireplace',
    name='Камин',
    room_slug='open-space',
    room_name='Гостиная, кухня и прихожая',
    live=True,
    lit_until=LIT_UNTIL,
)
COMMANDED_AT = datetime(2026, 11, 4, 16, 40, tzinfo=UTC)
CONVECTOR = SourceSettings(
    slug='heater-office',
    name='Конвектор: кабинет',
    room_slug='office',
    room_name='Кабинет',
    kind=HeatSourceKind.HEATER,
    device_slug='heater-office',
    priority=1,
    live=False,
    setpoint_bias=Decimal('0.0'),
    max_setpoint=Decimal('24.0'),
    min_outdoor=None,
    write_interval=timedelta(minutes=10),
    rated_watts=None,
    manual_until=None,
    last_command_at=None,
    last_dry_run_at=None,
    commanded_power=None,
    commanded_setpoint=None,
    load=None,
    load_live=False,
    load_management_live=False,
)
STOVE = FireplaceRecord(
    slug='stove',
    name='Печь',
    room_slug='attic',
    room_name='Мансарда',
    live=False,
    lit_until=None,
)


@pytest.fixture
def f_open_space() -> Room:
    return RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая', floor=FloorFactory.create(level=1))


@pytest.fixture
def f_climate_unit() -> Device:
    return DeviceFactory.create(slug='heater-office', kind=DeviceKind.CLIMATE_UNIT, driver=DeviceDriver.UNSUPPORTED)


@pytest.fixture
def f_house(f_open_space: Room, f_climate_unit: Device) -> Room:
    attic = RoomFactory.create(slug='attic', name='Мансарда', floor=FloorFactory.create(level=3))
    HeatSourceFactory.create(slug='stove', name='Печь', room=attic)
    HeatSourceFactory.create(
        slug='fireplace',
        name='Камин',
        room=f_open_space,
        live=True,
        lit_until=LIT_UNTIL,
    )
    HeatSourceFactory.create(
        slug='heater-office',
        name='Конвектор: кабинет',
        room=RoomFactory.create(slug='office', name='Кабинет', floor=attic.floor),
        kind=HeatSourceKind.HEATER,
        device=f_climate_unit,
    )
    return f_open_space


@pytest.fixture
def convector_with_a_live_load(f_climate_unit: Device) -> None:
    source = HeatSourceFactory.create(
        slug='heater-office',
        name='Конвектор: кабинет',
        room=RoomFactory.create(slug='office', name='Кабинет'),
        kind=HeatSourceKind.HEATER,
        device=f_climate_unit,
        live=True,
        setpoint_bias=Decimal('0.5'),
        max_setpoint=Decimal('23.0'),
        min_write_minutes=5,
        rated_watts=2000,
    )
    SourceLoadFactory.create(slug='convector-office', heat_source=source, live=True)
    LoadSettingsFactory.create(load_management_live=True)


@pytest.mark.usefixtures('f_house')
def test_fireplaces_lists_only_fireplaces_in_floor_order() -> None:
    assert HeatSourceRepository.fireplaces() == [FIREPLACE, STOVE]


@pytest.mark.usefixtures('f_house')
@pytest.mark.parametrize(
    ('room', 'expected'),
    [(None, [FIREPLACE, STOVE]), ('attic', [STOVE]), ('office', [])],
    ids=['every_fireplace', 'the_fireplace_of_a_room', 'room_without_a_fireplace'],
)
def test_fireplaces_for_update_lock_before_they_read(room: str | None, expected: list[FireplaceRecord]) -> None:
    with CaptureQueriesContext(connection) as queries:
        fireplaces = HeatSourceRepository.fireplaces_for_update(room)

    assert (fireplaces, queries.captured_queries[0]['sql'].split(' ', 1)[0]) == (expected, 'UPDATE')


@pytest.mark.usefixtures('f_house')
@pytest.mark.parametrize('until', [LIT_UNTIL + timedelta(hours=1), None], ids=['marked', 'cleared'])
def test_save_lit_until_stores_the_mark_of_one_fireplace(until: datetime | None) -> None:
    HeatSourceRepository.save_lit_until('stove', until)

    assert dict(HeatSource.objects.values_list('slug', 'lit_until')) == {
        'fireplace': LIT_UNTIL,
        'stove': until,
        'heater-office': None,
    }


def test_database_refuses_a_fireplace_with_a_device(f_open_space: Room, f_climate_unit: Device) -> None:
    with pytest.raises(IntegrityError):
        HeatSourceFactory.create(room=f_open_space, device=f_climate_unit)


def test_database_refuses_a_heater_without_a_device(f_open_space: Room) -> None:
    with pytest.raises(IntegrityError):
        HeatSourceFactory.create(room=f_open_space, kind=HeatSourceKind.HEATER)


def test_database_refuses_two_sources_of_one_priority_in_a_room(f_open_space: Room) -> None:
    HeatSourceFactory.create(room=f_open_space)

    with pytest.raises(IntegrityError):
        HeatSourceFactory.create(room=f_open_space)


@pytest.mark.usefixtures('f_house')
def test_commanded_settings_list_only_heaters_and_air_conditioners_with_their_device() -> None:
    assert HeatSourceRepository.commanded_settings() == [CONVECTOR]


@pytest.mark.usefixtures('convector_with_a_live_load')
def test_commanded_settings_carry_the_command_data_and_the_gates_of_the_source_s_load() -> None:
    assert HeatSourceRepository.commanded_settings() == [
        replace(
            CONVECTOR,
            live=True,
            setpoint_bias=Decimal('0.5'),
            max_setpoint=Decimal('23.0'),
            write_interval=timedelta(minutes=5),
            rated_watts=2000,
            load='convector-office',
            load_live=True,
            load_management_live=True,
        ),
    ]


@pytest.mark.usefixtures('f_house')
def test_marked_command_stores_its_stamp_and_what_teplo_commanded() -> None:
    HeatSourceRepository.mark_commanded('heater-office', COMMANDED_AT, power=True, setpoint=Decimal(22))
    HeatSourceRepository.mark_commanded('heater-office', COMMANDED_AT, power=False, setpoint=None)
    HeatSourceRepository.mark_dry_run('heater-office', COMMANDED_AT - timedelta(minutes=1))

    settings = HeatSourceRepository.commanded_settings()[0]
    assert (
        settings.last_command_at,
        settings.commanded_power,
        settings.commanded_setpoint,
        settings.last_dry_run_at,
    ) == (COMMANDED_AT, False, Decimal(22), COMMANDED_AT - timedelta(minutes=1))


@pytest.mark.usefixtures('f_house')
def test_shed_stamps_the_source_switches_it_off_and_ends_its_hold() -> None:
    HeatSourceRepository.hold_by_hand('heater-office', COMMANDED_AT + timedelta(hours=2))
    HeatSourceRepository.mark_shed('heater-office', COMMANDED_AT)

    settings = HeatSourceRepository.commanded_settings()[0]
    assert (settings.last_command_at, settings.commanded_power, settings.manual_until) == (COMMANDED_AT, False, None)


@pytest.mark.usefixtures('f_house')
def test_hold_by_hand_stores_its_end() -> None:
    HeatSourceRepository.hold_by_hand('heater-office', COMMANDED_AT + timedelta(hours=2))

    assert HeatSourceRepository.commanded_settings()[0].manual_until == COMMANDED_AT + timedelta(hours=2)


def test_hold_by_hand_of_a_source_that_is_gone_raises() -> None:
    with pytest.raises(HeatSourceNotFoundError):
        HeatSourceRepository.hold_by_hand('heater-office', COMMANDED_AT)
