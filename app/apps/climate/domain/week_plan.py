from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise

from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.schedule import resolve_mode
from apps.climate.domain.zone import ComfortWindow

REFERENCE_MONDAY = date(2024, 1, 1)
DAY = timedelta(days=1)


@dataclass(frozen=True, kw_only=True)
class DayPeriod:
    mode: ScheduleMode
    start: int
    end: int


def day_periods(windows: Sequence[ComfortWindow], weekday: int, preheat_lead: timedelta) -> tuple[DayPeriod, ...]:
    day = REFERENCE_MONDAY + timedelta(days=weekday)
    midnight = datetime.combine(day, time(), tzinfo=UTC)
    moments = {midnight, midnight + DAY}
    for offset in (-1, 0, 1):
        other = day + timedelta(days=offset)
        for window in windows:
            if window.weekday != other.weekday():
                continue
            comfort_at = datetime.combine(other, window.start, tzinfo=UTC)
            moments.update((comfort_at - preheat_lead, comfort_at, datetime.combine(other, window.end, tzinfo=UTC)))
    periods: list[DayPeriod] = []
    for begin, finish in pairwise(sorted(moment for moment in moments if midnight <= moment <= midnight + DAY)):
        start, end = _minute(midnight, begin), _minute(midnight, finish)
        if end == start:
            continue
        mode = resolve_mode(windows, begin, preheat_lead)
        if periods and periods[-1].mode is mode:
            periods[-1] = DayPeriod(mode=mode, start=periods[-1].start, end=end)
        else:
            periods.append(DayPeriod(mode=mode, start=start, end=end))
    return tuple(periods)


def _minute(midnight: datetime, moment: datetime) -> int:
    return int((moment - midnight).total_seconds()) // 60
