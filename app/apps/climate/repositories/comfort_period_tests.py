from datetime import time

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.comfort_windows import StoredWindow, ZoneWeek
from apps.climate.domain.errors import ComfortWindowNotFoundError, ZoneNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.factories import ComfortPeriodFactory, ZoneFactory
from apps.climate.models import ComfortPeriod, Zone
from apps.climate.repositories.comfort_period import ComfortPeriodRepository

pytestmark = pytest.mark.django_db


def _stored(period: ComfortPeriod) -> StoredWindow:
    return StoredWindow(id=period.pk, weekday=period.weekday, start=period.start, end=period.end)


@pytest.fixture
def f_kitchen_periods() -> list[ComfortPeriod]:
    zone = ZoneFactory.create(slug='kitchen', name='Кухня')
    return [
        ComfortPeriodFactory.create(zone=zone, weekday=0, start=time(6, 30), end=time(23, 30)),
        ComfortPeriodFactory.create(zone=zone, weekday=5, start=time(14, 0), end=time(23, 30)),
        ComfortPeriodFactory.create(zone=zone, weekday=5, start=time(8, 0), end=time(12, 0)),
    ]


@pytest.fixture
def attic() -> Zone:
    return ZoneFactory.create(slug='attic', name='Чердак')


def test_week_lists_the_windows_of_a_zone_by_day_and_start_once_the_zone_is_locked(
    f_kitchen_periods: list[ComfortPeriod],
) -> None:
    monday, saturday_evening, saturday_morning = f_kitchen_periods

    with CaptureQueriesContext(connection) as queries:
        week = ComfortPeriodRepository.week_for_update('kitchen')

    assert ([query['sql'].split(' ', 1)[0] for query in queries.captured_queries], week) == (
        ['UPDATE', 'SELECT', 'SELECT'],
        ZoneWeek(
            slug='kitchen',
            name='Кухня',
            windows=(_stored(monday), _stored(saturday_morning), _stored(saturday_evening)),
        ),
    )


@pytest.mark.usefixtures('f_kitchen_periods', 'attic')
def test_weeks_list_every_zone_also_without_windows() -> None:
    assert [(week.slug, len(week.windows)) for week in ComfortPeriodRepository.weeks()] == [
        ('attic', 0),
        ('kitchen', 3),
    ]


@pytest.mark.usefixtures('attic')
def test_window_week_is_the_week_of_the_zone_the_window_belongs_to_once_that_zone_is_locked(
    f_kitchen_periods: list[ComfortPeriod],
) -> None:
    with CaptureQueriesContext(connection) as queries:
        week = ComfortPeriodRepository.window_week_for_update(f_kitchen_periods[1].pk)

    assert (
        [query['sql'].split(' ', 1)[0] for query in queries.captured_queries],
        week.slug,
        len(week.windows),
    ) == (['UPDATE', 'SELECT', 'SELECT'], 'kitchen', 3)


def test_week_of_an_unknown_zone_or_window_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ComfortPeriodRepository.week_for_update('attic')

    with pytest.raises(ComfortWindowNotFoundError):
        ComfortPeriodRepository.window_week_for_update(9999)


@pytest.mark.usefixtures('f_kitchen_periods')
def test_replaced_days_get_their_new_windows_and_the_other_days_keep_theirs() -> None:
    ComfortPeriodRepository.replace_days(
        'kitchen',
        {
            5: (ComfortWindow(weekday=5, start=time(9, 0), end=time(22, 0)),),
            6: (ComfortWindow(weekday=6, start=time(9, 0), end=time(22, 0)),),
        },
    )

    assert list(ComfortPeriod.objects.order_by('weekday', 'start').values_list('weekday', 'start', 'end')) == [
        (0, time(6, 30), time(23, 30)),
        (5, time(9, 0), time(22, 0)),
        (6, time(9, 0), time(22, 0)),
    ]


@pytest.mark.usefixtures('f_kitchen_periods')
def test_replaced_day_may_be_left_without_comfort() -> None:
    ComfortPeriodRepository.replace_days('kitchen', {0: ()})

    assert list(ComfortPeriod.objects.order_by('weekday', 'start').values_list('weekday', 'start')) == [
        (5, time(8, 0)),
        (5, time(14, 0)),
    ]


def test_replacing_the_days_of_an_unknown_zone_raises() -> None:
    with pytest.raises(ZoneNotFoundError):
        ComfortPeriodRepository.replace_days('attic', {0: ()})
