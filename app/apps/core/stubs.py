import asyncio
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
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


class TransactionStub:
    def __init__(self, meanwhile: Callable[[], object] | None) -> None:
        self._meanwhile = meanwhile
        self._outcomes: list[str] = []

    @property
    def outcomes(self) -> list[str]:
        return self._outcomes

    def __call__(self) -> AbstractContextManager[None]:
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._meanwhile is not None:
            self._meanwhile()
        committed = False
        try:
            yield
            committed = True
        finally:
            self._outcomes.append('committed' if committed else 'rolled_back')
