import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from apps.devices.domain.readings import Phase, PhaseCurrents
from apps.devices.services.state_store import UNAVAILABLE_AFTER

STALE_AFTER = timedelta(seconds=10)
MEAN_STEP = Decimal('0.01')


@dataclass(frozen=True, kw_only=True)
class PhaseMeterSnapshot:
    slug: str
    currents: PhaseCurrents | None
    ages: dict[Phase, timedelta]
    age: timedelta | None
    available: bool
    stale: bool
    stale_phases: frozenset[Phase]
    error_code: str | None
    error: str | None


@dataclass(frozen=True, kw_only=True)
class CurrentStatistics:
    mean: Decimal
    peak: Decimal
    count: int


@dataclass(frozen=True, kw_only=True)
class _Current:
    amps: Decimal
    read_at: datetime
    recorded_at: float


@dataclass(frozen=True, kw_only=True)
class _Totals:
    total: Decimal
    peak: Decimal
    count: int


class PhaseMeterStore:
    def __init__(
        self,
        stale_after: timedelta,
        unavailable_after: timedelta,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stale_after = stale_after
        self._unavailable_after = unavailable_after
        self._clock = clock
        self._currents: dict[str, dict[Phase, _Current]] = {}
        self._totals: dict[str, dict[Phase, _Totals]] = {}
        self._failures: dict[str, tuple[str, str]] = {}
        self._reported: dict[str, bool] = {}
        self._lock = threading.Lock()

    def record_current(self, slug: str, phase: Phase, amps: Decimal, read_at: datetime) -> None:
        with self._lock:
            self._currents.setdefault(slug, {})[phase] = _Current(amps=amps, read_at=read_at, recorded_at=self._clock())
            totals = self._totals.setdefault(slug, {})
            totals[phase] = _added(totals.get(phase), amps)
            self._failures.pop(slug, None)

    def record_failure(self, slug: str, error_code: str, error: str) -> None:
        with self._lock:
            self._failures[slug] = (error_code, error)

    def clear_failure(self, slug: str, error_code: str) -> None:
        with self._lock:
            if slug in self._failures and self._failures[slug][0] == error_code:
                del self._failures[slug]

    def snapshot(self, slug: str) -> PhaseMeterSnapshot:
        with self._lock:
            currents = dict(self._currents.get(slug, {}))
            error_code, error = self._failures.get(slug, (None, None))
        now = self._clock()
        phases = [phase for phase in Phase if phase in currents]
        ages = {phase: timedelta(seconds=now - currents[phase].recorded_at) for phase in phases}
        stale_phases = frozenset(phase for phase in Phase if phase not in ages or ages[phase] > self._stale_after)
        age = min(ages.values(), default=None)
        return PhaseMeterSnapshot(
            slug=slug,
            currents=_phase_currents(currents, phases),
            ages=ages,
            age=age,
            available=age is not None and age <= self._unavailable_after,
            stale=bool(stale_phases),
            stale_phases=stale_phases,
            error_code=error_code,
            error=error,
        )

    def capture_statistics(self, slug: str) -> dict[Phase, CurrentStatistics]:
        with self._lock:
            totals = self._totals.pop(slug, {})
        return {
            phase: CurrentStatistics(
                mean=(totals[phase].total / totals[phase].count).quantize(MEAN_STEP),
                peak=totals[phase].peak,
                count=totals[phase].count,
            )
            for phase in Phase
            if phase in totals
        }

    def last_reported(self, slug: str) -> bool | None:
        with self._lock:
            return self._reported.get(slug)

    def mark_reported(self, slug: str, available: bool) -> None:
        with self._lock:
            self._reported[slug] = available


def _added(totals: _Totals | None, amps: Decimal) -> _Totals:
    if totals is None:
        return _Totals(total=amps, peak=amps, count=1)

    return _Totals(total=totals.total + amps, peak=max(totals.peak, amps), count=totals.count + 1)


def _phase_currents(currents: dict[Phase, _Current], phases: list[Phase]) -> PhaseCurrents | None:
    if not phases:
        return None

    return PhaseCurrents(
        amps={phase: currents[phase].amps for phase in phases},
        read_at=min(currents[phase].read_at for phase in phases),
    )


phase_meter_store = PhaseMeterStore(stale_after=STALE_AFTER, unavailable_after=UNAVAILABLE_AFTER)
