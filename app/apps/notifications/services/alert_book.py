import threading

from apps.notifications.domain.alerts import AlertKey
from apps.notifications.domain.outage import StartupNote
from apps.notifications.domain.policy import AlertTrack


class AlertBook:
    def __init__(self) -> None:
        self._tracks: dict[AlertKey, AlertTrack] = {}
        self._startup: StartupNote | None = None
        self._lock = threading.Lock()

    def tracks(self) -> dict[AlertKey, AlertTrack]:
        with self._lock:
            return dict(self._tracks)

    def replace_tracks(self, tracks: dict[AlertKey, AlertTrack]) -> None:
        with self._lock:
            self._tracks = dict(tracks)

    def note_startup(self, note: StartupNote) -> None:
        with self._lock:
            self._startup = note

    def startup(self) -> StartupNote | None:
        with self._lock:
            return self._startup

    def forget_startup(self) -> None:
        with self._lock:
            self._startup = None


alert_book = AlertBook()
