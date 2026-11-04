from decimal import Decimal

import pytest

from apps.climate.domain.external_heat import AirPoint, FireWatch
from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode
from apps.climate.services.control_book import ControlBook

MEMORY = ZoneMemory(
    path=ControlPath.AIR,
    mode=ScheduleMode.COMFORT,
    air_state=AirState.HEAT,
    heat_target=Decimal('22.0'),
)


BURNING = FireWatch(
    points={'air-kitchen': (AirPoint(moment=60.0, air=Decimal('21.4')),)},
    fire_since=60.0,
)
CALM = FireWatch(points={'air-bedroom': (AirPoint(moment=60.0, air=Decimal('19.0')),)})


@pytest.fixture
def f_book() -> ControlBook:
    book = ControlBook()
    book.remember_zone('kitchen', MEMORY)
    book.remember_backup('bedroom', True)
    book.remember_fire('open-space', BURNING)
    book.remember_fire('bedroom', CALM)
    return book


def test_book_remembers_a_zone(f_book: ControlBook) -> None:
    assert f_book.zone('kitchen') == MEMORY


def test_unknown_zone_starts_empty() -> None:
    book = ControlBook()

    assert book.zone('kitchen') == ZoneMemory()


def test_backup_state_is_kept_per_room_and_starts_off(f_book: ControlBook) -> None:
    f_book.remember_backup('office', False)

    assert (f_book.backup_active('bedroom'), f_book.backup_active('office'), f_book.backup_active('attic')) == (
        True,
        False,
        False,
    )


def test_fire_watch_is_kept_per_room_and_starts_empty(f_book: ControlBook) -> None:
    assert (f_book.fire('open-space'), f_book.fire('bedroom'), f_book.fire('attic')) == (BURNING, CALM, FireWatch())


def test_burning_rooms_are_the_rooms_whose_watch_sees_a_fire(f_book: ControlBook) -> None:
    assert f_book.burning_rooms() == frozenset({'open-space'})
