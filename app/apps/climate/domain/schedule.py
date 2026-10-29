from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.zone import ComfortWindow

TRANSITION_HORIZON_DAYS = 8


@dataclass(frozen=True, kw_only=True)
class ScheduleTransition:
    mode: ScheduleMode
    at: datetime


def next_transition(
    windows: Sequence[ComfortWindow],
    now: datetime,
    preheat_lead: timedelta,
) -> ScheduleTransition | None:
    current = resolve_mode(windows, now, preheat_lead)
    for moment in _boundaries(windows, now, preheat_lead):
        mode = resolve_mode(windows, moment, preheat_lead)
        if mode is not current:
            return ScheduleTransition(mode=mode, at=moment)

    return None


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


def _boundaries(windows: Sequence[ComfortWindow], now: datetime, preheat_lead: timedelta) -> list[datetime]:
    moments: set[datetime] = set()
    for offset in range(TRANSITION_HORIZON_DAYS):
        day = now.date() + timedelta(days=offset)
        for window in windows:
            if window.weekday != day.weekday():
                continue
            start = datetime.combine(day, window.start, tzinfo=now.tzinfo)
            end = datetime.combine(day, window.end, tzinfo=now.tzinfo)
            moments.update((start - preheat_lead, start, end))
    return sorted(moment for moment in moments if moment > now)
