import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from apps.devices.domain.readings import PlugState, ThermostatState

UNAVAILABLE_AFTER = timedelta(minutes=10)


@dataclass(frozen=True, kw_only=True)
class DeviceSnapshot[TState]:
    slug: str
    state: TState | None
    age: timedelta | None
    available: bool
    error_code: str | None
    error: str | None


class DeviceStateStore[TState]:
    def __init__(self, unavailable_after: timedelta, clock: Callable[[], float] = time.monotonic) -> None:
        self._unavailable_after = unavailable_after
        self._clock = clock
        self._states: dict[str, TState] = {}
        self._recorded_at: dict[str, float] = {}
        self._failures: dict[str, tuple[str, str]] = {}
        self._reported: dict[str, bool] = {}
        self._lock = threading.Lock()

    def record_state(self, slug: str, state: TState) -> None:
        with self._lock:
            self._states[slug] = state
            self._recorded_at[slug] = self._clock()
            self._failures.pop(slug, None)

    def record_failure(self, slug: str, error_code: str, error: str) -> None:
        with self._lock:
            self._failures[slug] = (error_code, error)

    def snapshot(self, slug: str) -> DeviceSnapshot[TState]:
        with self._lock:
            state = self._states.get(slug)
            recorded_at = self._recorded_at.get(slug)
            error_code, error = self._failures.get(slug, (None, None))
        age = None if recorded_at is None else timedelta(seconds=self._clock() - recorded_at)
        return DeviceSnapshot(
            slug=slug,
            state=state,
            age=age,
            available=age is not None and age <= self._unavailable_after,
            error_code=error_code,
            error=error,
        )

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
