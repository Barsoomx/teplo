import asyncio
from datetime import datetime, timedelta


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
