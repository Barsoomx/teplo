from datetime import time

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.comfort_windows import RoomWeek, StoredWindow
from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.factories import ComfortPeriodFactory, RoomClimateFactory, ZoneFactory
from apps.climate.models import ComfortPeriod, RoomClimate
from apps.climate.repositories.comfort_period import ComfortPeriodRepository
from apps.house.domain.errors import RoomNotFoundError
from apps.house.factories import FloorFactory, RoomFactory

pytestmark = pytest.mark.django_db


def _stored(period: ComfortPeriod) -> StoredWindow:
    return StoredWindow(id=period.pk, weekday=period.weekday, start=period.start, end=period.end)


def _rows(*fields: str) -> list[tuple[object, ...]]:
    return list(ComfortPeriod.objects.order_by('weekday', 'start').values_list(*fields))


@pytest.fixture
def f_open_space_periods() -> list[ComfortPeriod]:
    climate = RoomClimateFactory.create(
        room=RoomFactory.create(
            slug='open-space',
            name='Гостиная, кухня и прихожая',
            floor=FloorFactory.create(level=1),
        ),
    )
    return [
        ComfortPeriodFactory.create(zone=None, room_climate=climate, weekday=0, start=time(6, 30), end=time(23, 30)),
        ComfortPeriodFactory.create(zone=None, room_climate=climate, weekday=5, start=time(14, 0), end=time(23, 30)),
        ComfortPeriodFactory.create(zone=None, room_climate=climate, weekday=5, start=time(8, 0), end=time(12, 0)),
    ]


@pytest.fixture
def bedroom() -> RoomClimate:
    return RoomClimateFactory.create(
        room=RoomFactory.create(slug='bedroom', name='Спальня', floor=FloorFactory.create(level=2)),
    )


@pytest.fixture
def followed_office() -> None:
    RoomClimateFactory.create(
        room=RoomFactory.create(slug='office', name='Кабинет', floor=FloorFactory.create(level=2)),
        follows_occupancy=True,
        vacant_after_minutes=30,
    )


@pytest.fixture
def f_living_room_tagged_periods(f_open_space_periods: list[ComfortPeriod]) -> list[ComfortPeriod]:
    living_room = ZoneFactory.create(slug='living-room')
    ComfortPeriod.objects.filter(pk__in=[period.pk for period in f_open_space_periods]).update(zone=living_room)
    ComfortPeriodFactory.create(zone=ZoneFactory.create(slug='kitchen'), weekday=5, start=time(8, 0), end=time(12, 0))
    return f_open_space_periods


def test_week_lists_the_windows_of_a_room_by_day_and_start_once_the_room_is_locked(
    f_open_space_periods: list[ComfortPeriod],
) -> None:
    monday, saturday_evening, saturday_morning = f_open_space_periods

    with CaptureQueriesContext(connection) as queries:
        week = ComfortPeriodRepository.week_for_update('open-space')

    assert ([query['sql'].split(' ', 1)[0] for query in queries.captured_queries], week) == (
        ['UPDATE', 'SELECT', 'SELECT'],
        RoomWeek(
            slug='open-space',
            name='Гостиная, кухня и прихожая',
            windows=(_stored(monday), _stored(saturday_morning), _stored(saturday_evening)),
        ),
    )


@pytest.mark.usefixtures('bedroom', 'f_open_space_periods')
def test_weeks_list_every_room_climate_in_floor_order_also_without_windows() -> None:
    assert [(week.slug, len(week.windows)) for week in ComfortPeriodRepository.weeks()] == [
        ('open-space', 3),
        ('bedroom', 0),
    ]


@pytest.mark.usefixtures('f_open_space_periods', 'followed_office')
def test_weeks_say_whether_the_room_follows_occupancy_and_after_how_long_it_is_empty() -> None:
    assert [
        (week.slug, week.follows_occupancy, week.vacant_after_minutes) for week in ComfortPeriodRepository.weeks()
    ] == [('open-space', False, 20), ('office', True, 30)]


@pytest.mark.usefixtures('f_living_room_tagged_periods')
def test_weeks_leave_out_the_windows_of_a_zone_its_room_does_not_hold() -> None:
    assert [(week.slug, len(week.windows)) for week in ComfortPeriodRepository.weeks()] == [('open-space', 3)]


@pytest.mark.usefixtures('bedroom')
def test_window_week_is_the_week_of_the_room_the_window_belongs_to_once_that_room_is_locked(
    f_open_space_periods: list[ComfortPeriod],
) -> None:
    with CaptureQueriesContext(connection) as queries:
        week = ComfortPeriodRepository.window_week_for_update(f_open_space_periods[1].pk)

    assert (
        [query['sql'].split(' ', 1)[0] for query in queries.captured_queries],
        week.slug,
        len(week.windows),
    ) == (['UPDATE', 'SELECT', 'SELECT'], 'open-space', 3)


def test_week_of_an_unknown_room_or_window_raises() -> None:
    with pytest.raises(RoomNotFoundError):
        ComfortPeriodRepository.week_for_update('attic')

    with pytest.raises(ComfortWindowNotFoundError):
        ComfortPeriodRepository.window_week_for_update(9999)


def test_window_of_a_zone_outside_room_comfort_has_no_week() -> None:
    period = ComfortPeriodFactory.create(zone=ZoneFactory.create(slug='kitchen'), weekday=0)

    with pytest.raises(ComfortWindowNotFoundError):
        ComfortPeriodRepository.window_week_for_update(period.pk)


def test_replaced_days_get_their_new_windows_and_the_other_days_keep_theirs(
    f_open_space_periods: list[ComfortPeriod],
) -> None:
    ComfortPeriodRepository.replace_days(
        'open-space',
        {
            5: (ComfortWindow(weekday=5, start=time(9, 0), end=time(22, 0)),),
            6: (ComfortWindow(weekday=6, start=time(9, 0), end=time(22, 0)),),
        },
    )

    climate = f_open_space_periods[0].room_climate_id
    assert _rows('weekday', 'start', 'end', 'zone', 'room_climate') == [
        (0, time(6, 30), time(23, 30), None, climate),
        (5, time(9, 0), time(22, 0), None, climate),
        (6, time(9, 0), time(22, 0), None, climate),
    ]


@pytest.mark.usefixtures('f_open_space_periods')
def test_replaced_day_may_be_left_without_comfort() -> None:
    ComfortPeriodRepository.replace_days('open-space', {0: ()})

    assert _rows('weekday', 'start') == [(5, time(8, 0)), (5, time(14, 0))]


def test_replaced_days_take_the_place_of_the_zone_tagged_windows_of_the_room_and_leave_other_zones_alone(
    f_living_room_tagged_periods: list[ComfortPeriod],
) -> None:
    saturday = (ComfortWindow(weekday=5, start=time(9, 0), end=time(22, 0)),)

    ComfortPeriodRepository.replace_days('open-space', {5: saturday})

    climate = f_living_room_tagged_periods[0].room_climate_id
    assert _rows('weekday', 'start', 'zone__slug', 'room_climate') == [
        (0, time(6, 30), 'living-room', climate),
        (5, time(8, 0), 'kitchen', None),
        (5, time(9, 0), None, climate),
    ]


def test_replacing_the_days_of_an_unknown_room_raises() -> None:
    with pytest.raises(RoomNotFoundError):
        ComfortPeriodRepository.replace_days('attic', {0: ()})


def test_database_refuses_a_window_of_neither_a_zone_nor_a_room() -> None:
    with pytest.raises(IntegrityError):
        ComfortPeriodFactory.create(zone=None, room_climate=None, weekday=1)
