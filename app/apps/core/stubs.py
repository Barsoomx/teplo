import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from apps.core.domain.settings import SettingChange


class MonotonicClockStub:
    def __init__(self, now: float) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class WallClockStub:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def advance(self, step: timedelta) -> None:
        self._now += step


class SleepStub:
    def __init__(self, timeline: list[object], wakeups: int) -> None:
        self._timeline = timeline
        self._wakeups = wakeups

    async def __call__(self, seconds: float) -> None:
        self._timeline.append(seconds)
        if self._wakeups == 0:
            raise asyncio.CancelledError

        self._wakeups -= 1


class SettingsRecorderStub:
    def __init__(self, error: Exception | None) -> None:
        self._error = error
        self._records: list[tuple[tuple[SettingChange, ...], datetime]] = []

    @property
    def records(self) -> list[tuple[tuple[SettingChange, ...], datetime]]:
        return self._records

    def record(self, changes: Sequence[SettingChange], at: datetime, write: Callable[[], None]) -> None:
        if self._error is not None:
            raise self._error

        write()
        self._records.append((tuple(changes), at))
