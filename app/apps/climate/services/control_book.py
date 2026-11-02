import threading

from apps.climate.domain.memory import ZoneMemory


class ControlBook:
    def __init__(self) -> None:
        self._zones: dict[str, ZoneMemory] = {}
        self._guard_active = False
        self._lock = threading.Lock()

    def zone(self, slug: str) -> ZoneMemory:
        with self._lock:
            return self._zones.get(slug, ZoneMemory())

    def remember_zone(self, slug: str, memory: ZoneMemory) -> None:
        with self._lock:
            self._zones[slug] = memory

    def guard_active(self) -> bool:
        with self._lock:
            return self._guard_active

    def remember_guard(self, active: bool) -> None:
        with self._lock:
            self._guard_active = active


control_book = ControlBook()
