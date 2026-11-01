import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from apps.devices.domain.readings import PlugState, ThermostatState

UNAVAILABLE_AFTER = timedelta(minutes=10)


class TimedReading(Protocol):
    @property
    def read_at(self) -> datetime: ...


@dataclass(frozen=True, kw_only=True)
class DeviceSnapshot[TState: TimedReading]:
    slug: str
    state: TState | None
    available: bool
    error_code: str | None
    error: str | None


class DeviceStateStore[TState: TimedReading]:
    def __init__(self, unavailable_after: timedelta) -> None:
        self._unavailable_after = unavailable_after
        self._states: dict[str, TState] = {}
        self._failures: dict[str, tuple[str, str]] = {}
        self._reported: dict[str, bool] = {}
        self._lock = threading.Lock()

    def record_state(self, slug: str, state: TState) -> None:
        with self._lock:
            self._states[slug] = state
            self._failures.pop(slug, None)

    def record_failure(self, slug: str, error_code: str, error: str) -> None:
        with self._lock:
            self._failures[slug] = (error_code, error)

    def snapshot(self, slug: str, now: datetime) -> DeviceSnapshot[TState]:
        with self._lock:
            state = self._states.get(slug)
            error_code, error = self._failures.get(slug, (None, None))
        available = state is not None and now - state.read_at <= self._unavailable_after
        return DeviceSnapshot(slug=slug, state=state, available=available, error_code=error_code, error=error)

    def last_reported(self, slug: str) -> bool | None:
        with self._lock:
            return self._reported.get(slug)

    def mark_reported(self, slug: str, available: bool) -> None:
        with self._lock:
            self._reported[slug] = available


ThermostatSnapshot = DeviceSnapshot[ThermostatState]
ThermostatStateStore = DeviceStateStore[ThermostatState]
PlugSnapshot = DeviceSnapshot[PlugState]
PlugStateStore = DeviceStateStore[PlugState]

thermostat_state_store = ThermostatStateStore(unavailable_after=UNAVAILABLE_AFTER)
plug_state_store = PlugStateStore(unavailable_after=UNAVAILABLE_AFTER)
