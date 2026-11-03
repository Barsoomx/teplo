import time
from collections.abc import Callable
from datetime import timedelta

from apps.notifications.domain.alerts import PhaseAlarm
from apps.power.domain.loads import LoadPhase
from apps.power.domain.phase_watch import PhaseWatch
from apps.power.services.guard_book import GuardBook, guard_book


class PhaseOverloadSource:
    def __init__(self, book: GuardBook, clock: Callable[[], float]) -> None:
        self._book = book
        self._clock = clock

    @classmethod
    def init(cls) -> 'PhaseOverloadSource':
        return cls(book=guard_book, clock=time.monotonic)

    def phases(self) -> list[PhaseAlarm]:
        state = self._book.state()
        if state is None:
            return []

        now = self._clock()
        return [_alarm(phase, watch, now) for phase, watch in state.phases.watches.items()]


def _alarm(phase: LoadPhase, watch: PhaseWatch, now: float) -> PhaseAlarm:
    return PhaseAlarm(
        phase=phase.value,
        name=phase.value.upper(),
        alarmed=watch.alarmed,
        amps=watch.alarm_amps,
        began_ago=None if watch.alarm_since is None else timedelta(seconds=now - watch.alarm_since),
    )
