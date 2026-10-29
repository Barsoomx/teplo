import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.devices.domain.readings import ThermostatState

UNAVAILABLE_AFTER = timedelta(minutes=10)


@dataclass(frozen=True, kw_only=True)
class ThermostatSnapshot:
    slug: str
    state: ThermostatState | None
    available: bool
    error_code: str | None
    error: str | None


class ThermostatStateStore:
    def __init__(self, unavailable_after: timedelta) -> None:
        self._unavailable_after = unavailable_after
        self._states: dict[str, ThermostatState] = {}
        self._failures: dict[str, tuple[str, str]] = {}
        self._reported: dict[str, bool] = {}
        self._lock = threading.Lock()

    def record_state(self, slug: str, state: ThermostatState) -> None:
        with self._lock:
            self._states[slug] = state
            self._failures.pop(slug, None)

    def record_failure(self, slug: str, error_code: str, error: str) -> None:
        with self._lock:
            self._failures[slug] = (error_code, error)

    def snapshot(self, slug: str, now: datetime) -> ThermostatSnapshot:
        with self._lock:
            state = self._states.get(slug)
            error_code, error = self._failures.get(slug, (None, None))
        available = state is not None and now - state.read_at <= self._unavailable_after
        return ThermostatSnapshot(slug=slug, state=state, available=available, error_code=error_code, error=error)

    def last_reported(self, slug: str) -> bool | None:
        with self._lock:
            return self._reported.get(slug)

    def mark_reported(self, slug: str, available: bool) -> None:
        with self._lock:
            self._reported[slug] = available


thermostat_state_store = ThermostatStateStore(unavailable_after=UNAVAILABLE_AFTER)
