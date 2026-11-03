from datetime import time, timedelta

from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.week_plan import DayPeriod, day_periods
from apps.climate.domain.zone import ComfortWindow

LEAD = timedelta(hours=3)
EVERY_MONDAY = (ComfortWindow(weekday=0, start=time(6, 30), end=time(23, 30)),)


def test_day_with_a_comfort_window_goes_night_warm_up_comfort_night() -> None:
    assert day_periods(EVERY_MONDAY, 0, LEAD) == (
        DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=210),
        DayPeriod(mode=ScheduleMode.PREHEAT, start=210, end=390),
        DayPeriod(mode=ScheduleMode.COMFORT, start=390, end=1410),
        DayPeriod(mode=ScheduleMode.NIGHT, start=1410, end=1440),
    )


def test_day_without_comfort_is_night_all_day() -> None:
    assert (day_periods(EVERY_MONDAY, 2, LEAD), day_periods(EVERY_MONDAY, 6, LEAD)) == (
        (DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=1440),),
        (DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=1440),),
    )


def test_warm_up_before_comfort_after_midnight_starts_the_evening_before() -> None:
    windows = (ComfortWindow(weekday=1, start=time(1, 0), end=time(8, 0)),)

    assert (day_periods(windows, 0, LEAD), day_periods(windows, 1, LEAD)) == (
        (
            DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=1320),
            DayPeriod(mode=ScheduleMode.PREHEAT, start=1320, end=1440),
        ),
        (
            DayPeriod(mode=ScheduleMode.PREHEAT, start=0, end=60),
            DayPeriod(mode=ScheduleMode.COMFORT, start=60, end=480),
            DayPeriod(mode=ScheduleMode.NIGHT, start=480, end=1440),
        ),
    )


def test_warm_up_after_a_short_break_starts_when_the_window_before_ends() -> None:
    windows = (
        ComfortWindow(weekday=0, start=time(6, 30), end=time(12, 0)),
        ComfortWindow(weekday=0, start=time(13, 0), end=time(23, 0)),
    )

    assert day_periods(windows, 0, LEAD) == (
        DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=210),
        DayPeriod(mode=ScheduleMode.PREHEAT, start=210, end=390),
        DayPeriod(mode=ScheduleMode.COMFORT, start=390, end=720),
        DayPeriod(mode=ScheduleMode.PREHEAT, start=720, end=780),
        DayPeriod(mode=ScheduleMode.COMFORT, start=780, end=1380),
        DayPeriod(mode=ScheduleMode.NIGHT, start=1380, end=1440),
    )


def test_windows_that_touch_read_as_one_comfort_period() -> None:
    windows = (
        ComfortWindow(weekday=0, start=time(6, 0), end=time(12, 0)),
        ComfortWindow(weekday=0, start=time(12, 0), end=time(18, 0)),
    )

    assert day_periods(windows, 0, timedelta(0)) == (
        DayPeriod(mode=ScheduleMode.NIGHT, start=0, end=360),
        DayPeriod(mode=ScheduleMode.COMFORT, start=360, end=1080),
        DayPeriod(mode=ScheduleMode.NIGHT, start=1080, end=1440),
    )
