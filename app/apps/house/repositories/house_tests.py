import pytest

from apps.house.domain.rooms import FloorRecord, RoomRecord
from apps.house.factories import FloorFactory, RoomFactory
from apps.house.repositories.house import HouseRepository

pytestmark = pytest.mark.django_db


@pytest.fixture
def ground_floor() -> None:
    floor = FloorFactory.create(slug='floor-1', name='1 этаж', level=1)
    RoomFactory.create(slug='bathroom-1f', name='Санузел 1 этаж', floor=floor, position=2)
    RoomFactory.create(slug='pantry', name='Кладовая', floor=floor, position=1)
    RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая', floor=floor, position=1)


@pytest.fixture
def floors() -> None:
    FloorFactory.create(slug='floor-2', name='2 этаж', level=2)
    RoomFactory.create(slug='open-space', floor=FloorFactory.create(slug='floor-1', name='1 этаж', level=1))
    FloorFactory.create(slug='basement', name='Подвал', level=-1)


@pytest.fixture
def house() -> None:
    upstairs = FloorFactory.create(slug='floor-2', name='2 этаж', level=2)
    ground = FloorFactory.create(slug='floor-1', name='1 этаж', level=1)
    RoomFactory.create(slug='office', name='Кабинет', floor=upstairs, position=2)
    RoomFactory.create(slug='bathroom-1f', name='Санузел 1 этаж', floor=ground, position=2)
    RoomFactory.create(slug='bedroom', name='Спальня', floor=upstairs, position=1)
    RoomFactory.create(slug='open-space', name='Гостиная, кухня и прихожая', floor=ground, position=1)


@pytest.mark.usefixtures('ground_floor')
def test_floors_list_their_rooms_by_position_then_name() -> None:
    assert HouseRepository.floors() == [
        FloorRecord(
            slug='floor-1',
            name='1 этаж',
            level=1,
            rooms=(
                RoomRecord(
                    slug='open-space',
                    name='Гостиная, кухня и прихожая',
                    floor_slug='floor-1',
                    floor_level=1,
                    position=1,
                ),
                RoomRecord(slug='pantry', name='Кладовая', floor_slug='floor-1', floor_level=1, position=1),
                RoomRecord(slug='bathroom-1f', name='Санузел 1 этаж', floor_slug='floor-1', floor_level=1, position=2),
            ),
        ),
    ]


@pytest.mark.usefixtures('floors')
def test_floors_are_ordered_by_level_and_keep_a_floor_without_rooms() -> None:
    assert [
        (floor.slug, floor.level, tuple(room.slug for room in floor.rooms)) for floor in HouseRepository.floors()
    ] == [
        ('basement', -1, ()),
        ('floor-1', 1, ('open-space',)),
        ('floor-2', 2, ()),
    ]


@pytest.mark.usefixtures('house')
def test_rooms_are_listed_in_floor_order_with_their_floor() -> None:
    assert [(room.slug, room.floor_slug, room.floor_level, room.position) for room in HouseRepository.rooms()] == [
        ('open-space', 'floor-1', 1, 1),
        ('bathroom-1f', 'floor-1', 1, 2),
        ('bedroom', 'floor-2', 2, 1),
        ('office', 'floor-2', 2, 2),
    ]
