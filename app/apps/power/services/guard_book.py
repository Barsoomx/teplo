import threading
from collections.abc import Sequence

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.guard import GuardState
from apps.power.domain.headroom import Currents
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.meter_watch import MeterCondition
from apps.power.domain.start_gate import StartDecision, decide_start


class GuardBook:
    def __init__(self) -> None:
        self._state: GuardState | None = None
        self._last_permit_at: float | None = None
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
        with self._lock:
            decision = decide_start(
                load,
                meter=meter,
                currents=currents,
                loads=loads,
                last_permit_at=self._last_permit_at,
                at=at,
                thresholds=thresholds,
            )
            if decision.granted:
                self._last_permit_at = at
            return decision


guard_book = GuardBook()
