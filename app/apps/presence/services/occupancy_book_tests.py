import pytest

from apps.presence.domain.occupancy import Occupancy
from apps.presence.services.occupancy_book import OccupancyBook


@pytest.fixture
def f_book() -> OccupancyBook:
    book = OccupancyBook()
    book.replace({'office': Occupancy.OCCUPIED, 'bedroom': Occupancy.UNKNOWN})
    return book


def test_book_starts_empty() -> None:
    assert OccupancyBook().snapshot() == {}


def test_replace_swaps_the_whole_book(f_book: OccupancyBook) -> None:
    f_book.replace({'office': Occupancy.VACANT})

    assert f_book.snapshot() == {'office': Occupancy.VACANT}


def test_snapshot_is_a_copy(f_book: OccupancyBook) -> None:
    snapshot = f_book.snapshot()
    snapshot['office'] = Occupancy.VACANT

    assert f_book.snapshot() == {'office': Occupancy.OCCUPIED, 'bedroom': Occupancy.UNKNOWN}
