from datetime import timedelta

from apps.core.stubs import WallClockStub
from apps.system.domain.clock import ClockStatus, ClockStatusCode


class ClockStub:
    def __init__(self, trusted: bool) -> None:
        self._trusted = trusted

    def status(self) -> ClockStatus:
        return ClockStatus(
            trusted=self._trusted,
            code=ClockStatusCode.NTP_SYNCHRONIZED if self._trusted else ClockStatusCode.NTP_NOT_SYNCHRONIZED,
            reason='stub',
        )


class SyncingClockStub:
    def __init__(self, wall_clock: WallClockStub, step: timedelta) -> None:
        self._wall_clock = wall_clock
        self._step = step

    def status(self) -> ClockStatus:
        self._wall_clock.advance(self._step)
        return ClockStatus(trusted=True, code=ClockStatusCode.NTP_SYNCHRONIZED, reason='stub')


class SteppingMonotonicClockStub:
    def __init__(self, now: float, wall_clock: WallClockStub, step: timedelta) -> None:
        self._now = now
        self._wall_clock = wall_clock
        self._step = step

    def __call__(self) -> float:
        self._wall_clock.advance(self._step)
        return self._now
