import pytest

from apps.house.factories import FloorFactory, RoomFactory
from apps.notifications.domain.commands import CommandRoom
from apps.notifications.services.command_rooms import HouseCommandRooms

pytestmark = pytest.mark.django_db


@pytest.fixture
def house() -> None:
    upstairs = FloorFactory.create(level=2)
    ground = FloorFactory.create(level=1)
    RoomFactory.create(slug='bedroom', name='Спальня', floor=upstairs, aliases='спальн')
    RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая', floor=ground, aliases='Гостин кухн')
    RoomFactory.create(slug='pantry', name='Кладовая', floor=ground, position=2)


@pytest.mark.usefixtures('house')
def test_rooms_give_the_bot_each_room_with_its_floor_level_and_stems() -> None:
    assert HouseCommandRooms.init().rooms() == [
        CommandRoom(slug='open-space', name='Гостиная, кухня и прихожая', level=1, stems=('гостин', 'кухн')),
        CommandRoom(slug='pantry', name='Кладовая', level=1, stems=()),
        CommandRoom(slug='bedroom', name='Спальня', level=2, stems=('спальн',)),
    ]
