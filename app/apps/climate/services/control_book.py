import threading

from apps.climate.domain.memory import ZoneMemory


class ControlBook:
    def __init__(self) -> None:
        self._zones: dict[str, ZoneMemory] = {}
        self._backups: dict[str, bool] = {}
        self._lock = threading.Lock()

    def zone(self, slug: str) -> ZoneMemory:
        with self._lock:
            return self._zones.get(slug, ZoneMemory())

    def remember_zone(self, slug: str, memory: ZoneMemory) -> None:
        with self._lock:
            self._zones[slug] = memory

    def backup_active(self, room: str) -> bool:
        with self._lock:
            return self._backups.get(room, False)

    def remember_backup(self, room: str, active: bool) -> None:
        with self._lock:
            self._backups[room] = active


control_book = ControlBook()
