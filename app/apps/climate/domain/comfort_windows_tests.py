from datetime import time

import pytest

from apps.climate.domain.comfort_windows import (
    RoomWeek,
    ScheduleEdit,
    StoredWindow,
    on_grid,
    schedule_edit,
    window_problems,
)
from apps.climate.domain.zone import ComfortWindow
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem

MONDAY = ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30))
SATURDAY_MORNING = ComfortWindow(weekday=5, start=time(8, 0), end=time(12, 0))
SATURDAY_EVENING = ComfortWindow(weekday=5, start=time(14, 0), end=time(23, 30))
WEEK = RoomWeek(
    slug='kitchen',
    name='Кухня',
    windows=(
        StoredWindow(id=1, weekday=0, start=time(6, 30), end=time(23, 30)),
        StoredWindow(id=3, weekday=5, start=time(14, 0), end=time(23, 30)),
        StoredWindow(id=2, weekday=5, start=time(8, 0), end=time(12, 0)),
    ),
)


@pytest.mark.parametrize(
    ('moment', 'expected'),
    [
        (time(0, 0), True),
        (time(6, 15), True),
        (time(23, 45), True),
        (time(6, 40), False),
        (time(23, 59), False),
        (time(6, 30, 30), False),
    ],
)
def test_on_grid_takes_quarter_hours_only(moment: time, expected: bool) -> None:
    assert on_grid(moment) is expected


def test_week_lists_a_day_from_its_earliest_window() -> None:
    assert (WEEK.day(5), WEEK.day(2), WEEK.stored(3), WEEK.stored(9)) == (
        (SATURDAY_MORNING, SATURDAY_EVENING),
        (),
        StoredWindow(id=3, weekday=5, start=time(14, 0), end=time(23, 30)),
        None,
    )


@pytest.mark.parametrize(
    ('window', 'problems'),
    [
        (ComfortWindow(weekday=0, start=time(5, 0), end=time(6, 0)), ()),
        (ComfortWindow(weekday=0, start=time(23, 30), end=time(23, 45)), ()),
        (
            ComfortWindow(weekday=0, start=time(5, 10), end=time(6, 0)),
            (SettingProblem(field='start', code='off_grid'),),
        ),
        (ComfortWindow(weekday=0, start=time(5, 0), end=time(6, 5)), (SettingProblem(field='end', code='off_grid'),)),
        (
            ComfortWindow(weekday=0, start=time(6, 0), end=time(5, 0)),
            (SettingProblem(field='end', code='not_after_start'),),
        ),
        (
            ComfortWindow(weekday=0, start=time(5, 0), end=time(5, 0)),
            (SettingProblem(field='end', code='not_after_start'),),
        ),
        (ComfortWindow(weekday=0, start=time(23, 0), end=time(23, 45)), (SettingProblem(field='', code='overlap'),)),
        (ComfortWindow(weekday=0, start=time(7, 0), end=time(8, 0)), (SettingProblem(field='', code='overlap'),)),
    ],
    ids=[
        'before_the_day',
        'right_after',
        'start_off_the_grid',
        'end_off_the_grid',
        'backwards',
        'empty',
        'overlapping_the_end',
        'inside_another',
    ],
)
def test_window_problems_check_the_grid_the_order_and_overlaps(
    window: ComfortWindow,
    problems: tuple[SettingProblem, ...],
) -> None:
    assert window_problems((MONDAY, window), (window,)) == problems


def test_window_problems_leave_an_overlap_between_windows_the_panel_did_not_write() -> None:
    window = ComfortWindow(weekday=0, start=time(5, 0), end=time(6, 0))
    others = (MONDAY, ComfortWindow(weekday=0, start=time(7, 0), end=time(8, 0)))

    assert window_problems((*others, window), (window,)) == ()


def test_schedule_edit_journals_the_day_before_and_after() -> None:
    added = ComfortWindow(weekday=5, start=time(12, 0), end=time(13, 0))

    edit = schedule_edit(WEEK, {5: (SATURDAY_MORNING, SATURDAY_EVENING, added)}, written=(added,))

    assert edit == ScheduleEdit(
        changes=(
            SettingChange(
                target='schedule',
                subject='kitchen',
                name='Кухня',
                field='saturday',
                old=('08:00-12:00', '14:00-23:30'),
                new=('08:00-12:00', '12:00-13:00', '14:00-23:30'),
            ),
        ),
        days={5: (SATURDAY_MORNING, added, SATURDAY_EVENING)},
    )


def test_schedule_edit_leaves_out_the_days_that_do_not_change() -> None:
    edit = schedule_edit(WEEK, {0: (MONDAY,), 2: (), 5: (SATURDAY_EVENING, SATURDAY_MORNING)}, written=(MONDAY,))

    assert edit == ScheduleEdit(changes=(), days={})


def test_schedule_edit_journals_a_day_left_without_comfort() -> None:
    edit = schedule_edit(WEEK, {0: ()}, written=())

    assert edit == ScheduleEdit(
        changes=(
            SettingChange(
                target='schedule',
                subject='kitchen',
                name='Кухня',
                field='monday',
                old=('06:30-23:30',),
                new=(),
            ),
        ),
        days={0: ()},
    )


@pytest.mark.parametrize(
    ('days', 'written', 'problems'),
    [
        (
            {0: (MONDAY, ComfortWindow(weekday=0, start=time(23, 0), end=time(23, 45)))},
            (ComfortWindow(weekday=0, start=time(23, 0), end=time(23, 45)),),
            (SettingProblem(field='', code='overlap'),),
        ),
        ({7: ()}, (), (SettingProblem(field='weekday', code='out_of_range'),)),
        ({1: (MONDAY,)}, (MONDAY,), (SettingProblem(field='weekday', code='out_of_range'),)),
    ],
    ids=['overlap', 'weekday_outside_the_week', 'window_of_another_day'],
)
def test_schedule_edit_refuses_a_day_with_problems(
    days: dict[int, tuple[ComfortWindow, ...]],
    written: tuple[ComfortWindow, ...],
    problems: tuple[SettingProblem, ...],
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        schedule_edit(WEEK, days, written=written)

    assert raised.value.problems == problems
