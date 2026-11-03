from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import time

from apps.climate.domain.zone import ComfortWindow
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem

SCHEDULE = 'schedule'
WINDOW_STEP_MINUTES = 15
WEEKDAYS = range(7)
WEEKDAY_FIELDS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


@dataclass(frozen=True, kw_only=True)
class StoredWindow:
    id: int
    weekday: int
    start: time
    end: time

    @property
    def window(self) -> ComfortWindow:
        return ComfortWindow(weekday=self.weekday, start=self.start, end=self.end)


@dataclass(frozen=True, kw_only=True)
class RoomWeek:
    slug: str
    name: str
    windows: tuple[StoredWindow, ...]

    def day(self, weekday: int) -> tuple[ComfortWindow, ...]:
        return ordered(stored.window for stored in self.windows if stored.weekday == weekday)

    def stored(self, window_id: int) -> StoredWindow | None:
        return next((stored for stored in self.windows if stored.id == window_id), None)


@dataclass(frozen=True, kw_only=True)
class ScheduleEdit:
    changes: tuple[SettingChange, ...]
    days: dict[int, tuple[ComfortWindow, ...]]


def ordered(windows: Iterable[ComfortWindow]) -> tuple[ComfortWindow, ...]:
    return tuple(sorted(windows, key=lambda window: (window.start, window.end)))


def on_grid(moment: time) -> bool:
    return moment.second == 0 and moment.microsecond == 0 and moment.minute % WINDOW_STEP_MINUTES == 0


def window_text(window: ComfortWindow) -> str:
    return f'{window.start:%H:%M}-{window.end:%H:%M}'


def window_problems(day: Sequence[ComfortWindow], written: Sequence[ComfortWindow]) -> tuple[SettingProblem, ...]:
    problems: list[SettingProblem] = []
    for window in written:
        if not on_grid(window.start):
            problems.append(SettingProblem(field='start', code='off_grid'))
        if not on_grid(window.end):
            problems.append(SettingProblem(field='end', code='off_grid'))
        if window.end <= window.start:
            problems.append(SettingProblem(field='end', code='not_after_start'))
        if any(_overlap(window, other) for other in _others(day, window)):
            problems.append(SettingProblem(field='', code='overlap'))
    return tuple(dict.fromkeys(problems))


def schedule_edit(
    week: RoomWeek,
    days: Mapping[int, Sequence[ComfortWindow]],
    written: Sequence[ComfortWindow],
) -> ScheduleEdit:
    weekdays = sorted(days)
    problems = tuple(
        dict.fromkeys(
            problem
            for weekday in weekdays
            for problem in _day_problems(weekday, days[weekday], [w for w in written if w.weekday == weekday])
        ),
    )
    if problems:
        raise SettingsRejectedError(problems)

    changed = {weekday: ordered(days[weekday]) for weekday in weekdays if week.day(weekday) != ordered(days[weekday])}
    return ScheduleEdit(
        changes=tuple(
            SettingChange(
                target=SCHEDULE,
                subject=week.slug,
                name=week.name,
                field=WEEKDAY_FIELDS[weekday],
                old=tuple(window_text(window) for window in week.day(weekday)),
                new=tuple(window_text(window) for window in windows),
            )
            for weekday, windows in changed.items()
        ),
        days=changed,
    )


def _day_problems(
    weekday: int,
    windows: Sequence[ComfortWindow],
    written: Sequence[ComfortWindow],
) -> tuple[SettingProblem, ...]:
    if weekday not in WEEKDAYS or any(window.weekday != weekday for window in windows):
        return (SettingProblem(field='weekday', code='out_of_range'),)

    return window_problems(windows, written)


def _others(day: Sequence[ComfortWindow], window: ComfortWindow) -> list[ComfortWindow]:
    others = list(day)
    if window in others:
        others.remove(window)
    return others


def _overlap(window: ComfortWindow, other: ComfortWindow) -> bool:
    return other.start < window.end and window.start < other.end
