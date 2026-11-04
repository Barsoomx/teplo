from datetime import UTC, datetime, timedelta

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.heat_sources import FireplaceRecord, HeatSourceKind
from apps.climate.factories import HeatSourceFactory
from apps.climate.models import HeatSource
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.models import Device
from apps.house.factories import FloorFactory, RoomFactory
from apps.house.models import Room

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
        room=RoomFactory.create(slug='office', floor=attic.floor),
        kind=HeatSourceKind.HEATER,
        device=f_climate_unit,
    )
    return f_open_space


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
