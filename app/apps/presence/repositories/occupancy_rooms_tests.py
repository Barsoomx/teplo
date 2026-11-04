from datetime import timedelta

import pytest

from apps.climate.factories import RoomClimateFactory, ZoneFactory
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.house.factories import FloorFactory, RoomFactory
from apps.house.models import Room
from apps.presence.domain.rooms import OccupancyRoom
from apps.presence.repositories.occupancy_rooms import OccupancyRoomRepository

pytestmark = pytest.mark.django_db


def _presence(slug: str, room: Room | None) -> None:
    DeviceFactory.create(
        slug=slug,
        kind=DeviceKind.PRESENCE_SENSOR,
        driver=DeviceDriver.ESPHOME_PRESENCE,
        room=room,
    )


@pytest.fixture
def house() -> None:
    ground = FloorFactory.create(level=1)
    upstairs = FloorFactory.create(level=2)
    office = RoomFactory.create(slug='office', name='Кабинет', floor=upstairs, position=2)
    bedroom = RoomFactory.create(slug='bedroom', name='Спальня', floor=upstairs, position=1)
    open_space = RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая', floor=ground, position=1)
    bathroom = RoomFactory.create(slug='bathroom-1f', name='Санузел 1 этаж', floor=ground, position=2)
    RoomClimateFactory.create(room=office, follows_occupancy=True, vacant_after_minutes=30)
    RoomClimateFactory.create(room=open_space, follows_occupancy=True)
    RoomClimateFactory.create(room=bathroom)
    ZoneFactory.create(slug='living-room', room=open_space)
    _presence('presence-office', office)
    _presence('pc-office', office)
    _presence('presence-bedroom', bedroom)
    _presence('presence-living-room', open_space)
    _presence('presence-attic', None)
    DeviceFactory.create(
        slug='air-bathroom',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        driver=DeviceDriver.ESPHOME_TEMPERATURE,
        room=bathroom,
    )


@pytest.mark.usefixtures('house')
def test_occupancy_rooms_are_the_rooms_holding_a_presence_source_in_house_order() -> None:
    assert OccupancyRoomRepository.occupancy_rooms() == [
        OccupancyRoom(
            slug='open-space',
            name='Гостиная, кухня и прихожая',
            vacant_after=timedelta(minutes=20),
            follows_occupancy=False,
            sources=('presence-living-room',),
        ),
        OccupancyRoom(
            slug='bedroom',
            name='Спальня',
            vacant_after=timedelta(minutes=20),
            follows_occupancy=False,
            sources=('presence-bedroom',),
        ),
        OccupancyRoom(
            slug='office',
            name='Кабинет',
            vacant_after=timedelta(minutes=30),
            follows_occupancy=True,
            sources=('pc-office', 'presence-office'),
        ),
    ]


def test_house_without_a_presence_source_has_no_occupancy_room() -> None:
    assert OccupancyRoomRepository.occupancy_rooms() == []
