from datetime import timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.external_heat import AirPoint, FireWatch
from apps.climate.domain.fast_source import SourceTarget
from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode
from apps.climate.services.control_book import ControlBook
from apps.core.stubs import MonotonicClockStub

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
JOURNALED = SourceTarget(power=True, setpoint=Decimal(22))
BY_OCCUPANCY = SourceTarget(power=True, setpoint=Decimal(19))


@pytest.fixture
def f_book() -> ControlBook:
    book = ControlBook()
    book.remember_zone('kitchen', MEMORY)
    book.remember_backup('bedroom', True)
    book.remember_fire('open-space', BURNING)
    book.remember_fire('bedroom', CALM)
    book.remember_source_journaled('convector-office', JOURNALED)
    book.remember_occupancy_journaled('convector-office', BY_OCCUPANCY)
    book.remember_occupancy_journaled('ac-bedroom', BY_OCCUPANCY)
    book.remember_lockout('ac-bedroom', True)
    book.remember_lockout('ac-office', False)
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


def test_book_remembers_the_target_last_journaled_for_each_heat_source(f_book: ControlBook) -> None:
    assert (f_book.source_journaled('convector-office'), f_book.source_journaled('ac-bedroom')) == (JOURNALED, None)


def test_book_notices_a_room_without_its_climate_once(f_book: ControlBook) -> None:
    first = f_book.first_notice_without_climate('office')

    assert (first, f_book.first_notice_without_climate('office')) == (True, False)


def test_book_remembers_what_occupancy_would_give_each_source_until_it_is_forgotten(f_book: ControlBook) -> None:
    f_book.forget_occupancy_journaled('ac-bedroom')
    f_book.forget_occupancy_journaled('heater-attic')

    assert (
        f_book.occupancy_journaled('convector-office'),
        f_book.occupancy_journaled('ac-bedroom'),
        f_book.source_journaled('convector-office'),
    ) == (BY_OCCUPANCY, None, JOURNALED)


def test_book_remembers_each_source_s_outdoor_lockout_and_starts_unlocked(f_book: ControlBook) -> None:
    assert (
        f_book.source_locked_out('ac-bedroom'),
        f_book.source_locked_out('ac-office'),
        f_book.source_locked_out('ac-attic'),
    ) == (True, False, False)


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def f_book_that_lost_a_sensor_ten_minutes_ago(clock: MonotonicClockStub) -> ControlBook:
    book = ControlBook(clock=clock)
    book.air_lost_for('air-bedroom', lost=True)
    clock.advance(600)
    return book


def test_lost_sensor_counts_from_the_first_time_it_was_seen_lost(
    f_book_that_lost_a_sensor_ten_minutes_ago: ControlBook,
) -> None:
    assert (
        f_book_that_lost_a_sensor_ten_minutes_ago.air_lost_for('air-bedroom', lost=True),
        f_book_that_lost_a_sensor_ten_minutes_ago.air_lost_for('air-kitchen', lost=True),
    ) == (timedelta(minutes=10), timedelta(0))


def test_sensor_that_reads_again_is_counted_afresh_once_lost_again(
    f_book_that_lost_a_sensor_ten_minutes_ago: ControlBook,
) -> None:
    back = f_book_that_lost_a_sensor_ten_minutes_ago.air_lost_for('air-bedroom', lost=False)

    assert (back, f_book_that_lost_a_sensor_ten_minutes_ago.air_lost_for('air-bedroom', lost=True)) == (
        None,
        timedelta(0),
    )
