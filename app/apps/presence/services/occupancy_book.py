import threading
from collections.abc import Mapping

from apps.presence.domain.occupancy import Occupancy


class OccupancyBook:
    def __init__(self) -> None:
        self._occupancy: dict[str, Occupancy] = {}
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Occupancy]:
        with self._lock:
            return dict(self._occupancy)

    def replace(self, occupancy: Mapping[str, Occupancy]) -> None:
        with self._lock:
            self._occupancy = dict(occupancy)


occupancy_book = OccupancyBook()
