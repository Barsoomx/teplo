from decimal import Decimal

import pytest

from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode
from apps.climate.services.control_book import ControlBook

MEMORY = ZoneMemory(
    path=ControlPath.AIR,
    mode=ScheduleMode.COMFORT,
    air_state=AirState.HEAT,
    heat_target=Decimal('22.0'),
)


@pytest.fixture
def f_book() -> ControlBook:
    book = ControlBook()
    book.remember_zone('kitchen', MEMORY)
    book.remember_guard(True)
    return book


def test_book_remembers_zone_and_guard(f_book: ControlBook) -> None:
    assert (f_book.zone('kitchen'), f_book.guard_active()) == (MEMORY, True)


def test_unknown_zone_and_guard_start_empty() -> None:
    book = ControlBook()

    assert (book.zone('kitchen'), book.guard_active()) == (ZoneMemory(), False)
