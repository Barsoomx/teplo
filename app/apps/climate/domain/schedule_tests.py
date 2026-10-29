from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.schedule import resolve_mode
from apps.climate.domain.zone import ComfortWindow

MOSCOW = ZoneInfo('Europe/Moscow')
LEAD = timedelta(hours=3)


@pytest.fixture
def f_daily_windows() -> tuple[ComfortWindow, ...]:
    return tuple(ComfortWindow(weekday=weekday, start=time(6, 30), end=time(23, 30)) for weekday in range(7))


@pytest.fixture
def f_friday_early_window() -> tuple[ComfortWindow, ...]:
    return (ComfortWindow(weekday=4, start=time(1, 0), end=time(7, 0)),)


@pytest.mark.parametrize(
    ('moment', 'expected'),
    [
        (datetime(2026, 10, 29, 6, 30, tzinfo=MOSCOW), ScheduleMode.COMFORT),
        (datetime(2026, 10, 29, 12, 0, tzinfo=MOSCOW), ScheduleMode.COMFORT),
        (datetime(2026, 10, 29, 23, 30, tzinfo=MOSCOW), ScheduleMode.NIGHT),
        (datetime(2026, 10, 29, 1, 0, tzinfo=MOSCOW), ScheduleMode.NIGHT),
        (datetime(2026, 10, 29, 3, 29, tzinfo=MOSCOW), ScheduleMode.NIGHT),
        (datetime(2026, 10, 29, 3, 30, tzinfo=MOSCOW), ScheduleMode.PREHEAT),
        (datetime(2026, 10, 29, 6, 29, tzinfo=MOSCOW), ScheduleMode.PREHEAT),
    ],
)
def test_resolve_mode_for_daily_comfort(
    f_daily_windows: tuple[ComfortWindow, ...],
    moment: datetime,
    expected: ScheduleMode,
) -> None:
    assert resolve_mode(f_daily_windows, moment, LEAD) is expected


def test_preheat_starts_on_previous_day(f_friday_early_window: tuple[ComfortWindow, ...]) -> None:
    moment = datetime(2026, 10, 29, 22, 0, tzinfo=MOSCOW)

    assert resolve_mode(f_friday_early_window, moment, LEAD) is ScheduleMode.PREHEAT


def test_window_applies_only_on_its_weekday(f_friday_early_window: tuple[ComfortWindow, ...]) -> None:
    moment = datetime(2026, 10, 29, 2, 0, tzinfo=MOSCOW)

    assert resolve_mode(f_friday_early_window, moment, LEAD) is ScheduleMode.NIGHT
