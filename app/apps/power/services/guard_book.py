import threading
from collections.abc import Mapping, Sequence

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.guard import GuardState
from apps.power.domain.headroom import Currents
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.meter_watch import MeterCondition
from apps.power.domain.start_gate import StartDecision, StartRequest, decide_starts


class GuardBook:
    def __init__(self) -> None:
        self._state: GuardState | None = None
        self._last_permit_at: float | None = None
        self._waiting: frozenset[str] = frozenset()
        self._lock = threading.Lock()

    def state(self) -> GuardState | None:
        with self._lock:
            return self._state

    def remember(self, state: GuardState) -> None:
        with self._lock:
            self._state = state

    def held_zones(self) -> frozenset[str]:
        with self._lock:
            return frozenset() if self._state is None else self._state.held_zones

    def last_permit_at(self) -> float | None:
        with self._lock:
            return self._last_permit_at

    def permit_start(
        self,
        load: LoadStatus,
        *,
        meter: MeterCondition,
        currents: Currents,
        loads: Sequence[LoadStatus],
        at: float,
        thresholds: PhaseThresholds,
    ) -> StartDecision:
        decisions = self.serve_starts(
            (StartRequest(load=load, live=True),),
            meter=meter,
            currents=currents,
            loads=loads,
            at=at,
            thresholds=thresholds,
        )
        return decisions[load.slug]

    def serve_starts(
        self,
        requests: Sequence[StartRequest],
        *,
        meter: MeterCondition,
        currents: Currents,
        loads: Sequence[LoadStatus],
        at: float,
        thresholds: PhaseThresholds,
    ) -> Mapping[str, StartDecision]:
        with self._lock:
            served = decide_starts(
                requests,
                meter=meter,
                currents=currents,
                loads=loads,
                last_permit_at=self._last_permit_at,
                at=at,
                thresholds=thresholds,
            )
            self._last_permit_at = served.last_permit_at
            return served.decisions

    def waiting(self) -> frozenset[str]:
        with self._lock:
            return self._waiting

    def note_waiting(self, waiting: frozenset[str]) -> frozenset[str]:
        with self._lock:
            began = waiting - self._waiting
            self._waiting = waiting
            return began


guard_book = GuardBook()
