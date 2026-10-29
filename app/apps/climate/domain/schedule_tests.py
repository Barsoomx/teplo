from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.schedule import ScheduleTransition, next_transition, resolve_mode
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


@pytest.mark.parametrize(
    ('moment', 'expected'),
    [
        (
            datetime(2026, 10, 29, 12, 0, tzinfo=MOSCOW),
            ScheduleTransition(mode=ScheduleMode.NIGHT, at=datetime(2026, 10, 29, 23, 30, tzinfo=MOSCOW)),
        ),
        (
            datetime(2026, 10, 29, 23, 45, tzinfo=MOSCOW),
            ScheduleTransition(mode=ScheduleMode.PREHEAT, at=datetime(2026, 10, 30, 3, 30, tzinfo=MOSCOW)),
        ),
        (
            datetime(2026, 10, 29, 4, 0, tzinfo=MOSCOW),
            ScheduleTransition(mode=ScheduleMode.COMFORT, at=datetime(2026, 10, 29, 6, 30, tzinfo=MOSCOW)),
        ),
    ],
)
def test_next_transition_for_daily_comfort(
    f_daily_windows: tuple[ComfortWindow, ...],
    moment: datetime,
    expected: ScheduleTransition,
) -> None:
    assert next_transition(f_daily_windows, moment, LEAD) == expected


def test_next_transition_finds_a_window_days_ahead(f_friday_early_window: tuple[ComfortWindow, ...]) -> None:
    moment = datetime(2026, 10, 31, 12, 0, tzinfo=MOSCOW)

    assert next_transition(f_friday_early_window, moment, LEAD) == ScheduleTransition(
        mode=ScheduleMode.PREHEAT,
        at=datetime(2026, 11, 5, 22, 0, tzinfo=MOSCOW),
    )


def test_next_transition_without_windows_is_none() -> None:
    assert next_transition((), datetime(2026, 10, 29, 12, 0, tzinfo=MOSCOW), LEAD) is None
