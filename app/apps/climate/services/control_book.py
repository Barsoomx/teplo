import threading

from apps.climate.domain.external_heat import FireWatch
from apps.climate.domain.fast_source import SourceTarget
from apps.climate.domain.memory import ZoneMemory


class ControlBook:
    def __init__(self) -> None:
        self._zones: dict[str, ZoneMemory] = {}
        self._backups: dict[str, bool] = {}
        self._fires: dict[str, FireWatch] = {}
        self._journaled_sources: dict[str, SourceTarget] = {}
        self._rooms_without_climate: set[str] = set()
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

    def fire(self, room: str) -> FireWatch:
        with self._lock:
            return self._fires.get(room, FireWatch())

    def remember_fire(self, room: str, watch: FireWatch) -> None:
        with self._lock:
            self._fires[room] = watch

    def burning_rooms(self) -> frozenset[str]:
        with self._lock:
            return frozenset(room for room, watch in self._fires.items() if watch.burning)

    def source_journaled(self, source: str) -> SourceTarget | None:
        with self._lock:
            return self._journaled_sources.get(source)

    def remember_source_journaled(self, source: str, target: SourceTarget) -> None:
        with self._lock:
            self._journaled_sources[source] = target

    def first_notice_without_climate(self, room: str) -> bool:
        with self._lock:
            noticed = room not in self._rooms_without_climate
            self._rooms_without_climate.add(room)
            return noticed


control_book = ControlBook()
