from collections.abc import Sequence
from datetime import datetime, timedelta

from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.zone import ComfortWindow


def resolve_mode(windows: Sequence[ComfortWindow], now: datetime, preheat_lead: timedelta) -> ScheduleMode:
    occurrences = _occurrences(windows, now)
    if any(start <= now < end for start, end in occurrences):
        return ScheduleMode.COMFORT

    if any(start - preheat_lead <= now < start for start, _ in occurrences):
        return ScheduleMode.PREHEAT

    return ScheduleMode.NIGHT


def _occurrences(windows: Sequence[ComfortWindow], now: datetime) -> list[tuple[datetime, datetime]]:
    occurrences: list[tuple[datetime, datetime]] = []
    for offset in (-1, 0, 1):
        day = now.date() + timedelta(days=offset)
        occurrences.extend(
            (
                datetime.combine(day, window.start, tzinfo=now.tzinfo),
                datetime.combine(day, window.end, tzinfo=now.tzinfo),
            )
            for window in windows
            if window.weekday == day.weekday()
        )
    return occurrences
