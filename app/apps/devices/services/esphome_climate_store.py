import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from apps.devices.domain.readings import ClimateMode, ClimateUnitState


@dataclass(frozen=True, kw_only=True)
class EsphomeClimateSnapshot:
    slug: str
    state: ClimateUnitState | None
    age: timedelta | None
    error_code: str | None
    error: str | None


@dataclass(frozen=True, kw_only=True)
class _ModeReport:
    mode: ClimateMode
    read_at: datetime
    recorded_at: float


@dataclass(frozen=True, kw_only=True)
class _TargetReport:
    target: Decimal
    read_at: datetime
    recorded_at: float


class EsphomeClimateStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._modes: dict[str, _ModeReport] = {}
        self._targets: dict[str, _TargetReport] = {}
        self._room_temperatures: dict[str, Decimal | None] = {}
        self._failures: dict[str, tuple[str, str]] = {}
        self._changed = threading.Condition()

    def record_mode(self, slug: str, mode: ClimateMode, read_at: datetime) -> None:
        with self._changed:
            self._modes[slug] = _ModeReport(mode=mode, read_at=read_at, recorded_at=self._clock())
            self._recorded(slug)

    def record_target(self, slug: str, target: Decimal, read_at: datetime) -> None:
        with self._changed:
            self._targets[slug] = _TargetReport(target=target, read_at=read_at, recorded_at=self._clock())
            self._recorded(slug)

    def record_room_temperature(self, slug: str, temperature: Decimal | None) -> None:
        with self._changed:
            self._room_temperatures[slug] = temperature
            self._recorded(slug)

    def record_failure(self, slug: str, error_code: str, error: str) -> None:
        with self._changed:
            self._failures[slug] = (error_code, error)

    def clear_failure(self, slug: str, error_code: str) -> None:
        with self._changed:
            if slug in self._failures and self._failures[slug][0] == error_code:
                del self._failures[slug]

    def snapshot(self, slug: str) -> EsphomeClimateSnapshot:
        with self._changed:
            older = self._older_report(slug)
            state = self._state(slug)
            error_code, error = self._failures.get(slug, (None, None))
        return EsphomeClimateSnapshot(
            slug=slug,
            state=state,
            age=None if older is None else timedelta(seconds=self._clock() - older.recorded_at),
            error_code=error_code,
            error=error,
        )

    def wait_for(self, slug: str, accepts: Callable[[ClimateUnitState], bool], timeout: float) -> bool:
        with self._changed:
            return self._changed.wait_for(lambda: self._accepted(slug, accepts), timeout=timeout)

    def _recorded(self, slug: str) -> None:
        self._failures.pop(slug, None)
        self._changed.notify_all()

    def _accepted(self, slug: str, accepts: Callable[[ClimateUnitState], bool]) -> bool:
        state = self._state(slug)
        return state is not None and accepts(state)

    def _state(self, slug: str) -> ClimateUnitState | None:
        older = self._older_report(slug)
        if older is None:
            return None

        return ClimateUnitState(
            mode=self._modes[slug].mode,
            target=self._targets[slug].target,
            room_temperature=self._room_temperatures.get(slug),
            read_at=older.read_at,
        )

    def _older_report(self, slug: str) -> _ModeReport | _TargetReport | None:
        mode = self._modes.get(slug)
        target = self._targets.get(slug)
        if mode is None or target is None:
            return None

        return mode if mode.recorded_at <= target.recorded_at else target


esphome_climate_store = EsphomeClimateStore()
