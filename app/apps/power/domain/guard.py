from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from apps.power.domain.control import LoadControl
from apps.power.domain.ev_detection import EvWatch
from apps.power.domain.load_status import LoadReading, LoadStatus
from apps.power.domain.loads import MEASURED_PHASES, LearnedPhase, LoadPhase, LoadRecord
from apps.power.domain.meter_watch import MeterWatch
from apps.power.domain.phase_learning import LearningState
from apps.power.domain.phase_samples import SAMPLE_SPAN, PhaseSample
from apps.power.domain.phase_watch import PhaseWatch
from apps.power.domain.shedding import Shed, relief


@dataclass(frozen=True, kw_only=True)
class GuardLoads:
    records: tuple[LoadRecord, ...]
    control: LoadControl
    live_zones: frozenset[str]
    meter: str | None
    read_at: float

    def record(self, slug: str) -> LoadRecord | None:
        return next((record for record in self.records if record.slug == slug), None)

    def with_learned(self, slug: str, learned: LearnedPhase) -> 'GuardLoads':
        return replace(
            self,
            records=tuple(
                replace(record, learned=learned) if record.slug == slug else record for record in self.records
            ),
        )


@dataclass(frozen=True, kw_only=True)
class SeenReading:
    polled_at: datetime
    reading: LoadReading


@dataclass(frozen=True, kw_only=True)
class PhaseState:
    meter: MeterWatch
    currents: Mapping[LoadPhase, Decimal]
    watches: Mapping[LoadPhase, PhaseWatch]
    samples: tuple[PhaseSample, ...]

    @classmethod
    def started(cls, at: float) -> 'PhaseState':
        return cls(
            meter=MeterWatch.started(at),
            currents={},
            watches={phase: PhaseWatch() for phase in MEASURED_PHASES},
            samples=(),
        )


@dataclass(frozen=True, kw_only=True)
class ShedState:
    sheds: Mapping[str, Shed] = field(default_factory=dict)
    resumed: bool = False
    last_restore_at: float | None = None
    last_limp_shed_at: float | None = None
    failed: Mapping[str, float] = field(default_factory=dict)
    overridden: Mapping[str, LoadPhase] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class GuardState:
    loads: GuardLoads
    phases: PhaseState
    readings: Mapping[str, SeenReading]
    learning: LearningState
    ev: tuple[EvWatch, ...]
    shedding: ShedState

    @classmethod
    def started(cls, loads: GuardLoads, at: float) -> 'GuardState':
        return cls(
            loads=loads,
            phases=PhaseState.started(at),
            readings={},
            learning=LearningState(),
            ev=(),
            shedding=ShedState(),
        )

    @property
    def held_zones(self) -> frozenset[str]:
        live = {slug for slug, shed in self.shedding.sheds.items() if shed.live}
        return frozenset(
            record.zone_slug for record in self.loads.records if record.zone_slug is not None and record.slug in live
        )


def follow_samples(
    samples: tuple[PhaseSample, ...],
    currents: Mapping[LoadPhase, Decimal],
    at: float,
) -> tuple[PhaseSample, ...]:
    kept_since = at - SAMPLE_SPAN.total_seconds()
    kept = tuple(sample for sample in samples if sample.at >= kept_since)
    if any(phase not in currents for phase in MEASURED_PHASES):
        return kept

    return (*kept, PhaseSample(at=at, amps=dict(currents)))


def load_statuses(
    loads: GuardLoads,
    readings: Mapping[str, SeenReading],
    shedding: ShedState,
) -> tuple[LoadStatus, ...]:
    return tuple(
        LoadStatus.of(
            record,
            _usable(readings.get(record.slug), shedding.failed.get(record.slug)),
            shed=record.slug in shedding.sheds,
            default_amps=loads.control.default_amps,
        )
        for record in loads.records
    )


def relieved_currents(
    currents: Mapping[LoadPhase, Decimal],
    sheds: Iterable[Shed],
    at: float,
) -> dict[LoadPhase, Decimal]:
    kept = tuple(sheds)
    return {phase: current - relief(kept, phase, at) for phase, current in currents.items()}


def _usable(seen: SeenReading | None, failed_at: float | None) -> LoadReading | None:
    if seen is None or (failed_at is not None and seen.reading.read_at <= failed_at):
        return None

    return seen.reading
