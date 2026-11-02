import threading
from collections.abc import Sequence

from apps.power.domain.outage import PowerCut


class OutageBacklog:
    def __init__(self) -> None:
        self._power_cuts: list[PowerCut] = []
        self._lock = threading.Lock()

    def put(self, power_cut: PowerCut) -> None:
        with self._lock:
            self._power_cuts.append(power_cut)

    def put_back(self, power_cuts: Sequence[PowerCut]) -> None:
        with self._lock:
            self._power_cuts = [*power_cuts, *self._power_cuts]

    def take(self) -> tuple[PowerCut, ...]:
        with self._lock:
            power_cuts = tuple(self._power_cuts)
            self._power_cuts = []
        return power_cuts

    def pending(self) -> tuple[PowerCut, ...]:
        with self._lock:
            return tuple(self._power_cuts)


outage_backlog = OutageBacklog()
