from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.headroom import Currents, PhaseOverrun, overrun
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadKind
from apps.power.domain.meter_watch import MeterCondition, drawing_floors
from apps.power.domain.moments import elapsed


class StartRefusal(StrEnum):
    METER_STALE = 'meter_stale'
    PHASE_TAKEN = 'phase_taken'
    TOO_SOON = 'too_soon'
    NO_HEADROOM = 'no_headroom'


@dataclass(frozen=True, kw_only=True)
class StartDecision:
    load: str
    refusal: StartRefusal | None
    overrun: PhaseOverrun | None

    @property
    def granted(self) -> bool:
        return self.refusal is None


def decide_start(
    load: LoadStatus,
    *,
    meter: MeterCondition,
    currents: Currents,
    loads: Sequence[LoadStatus],
    last_permit_at: float | None,
    at: float,
    thresholds: PhaseThresholds,
) -> StartDecision:
    refusal = _refusal(load, meter, loads, last_permit_at, at, thresholds)
    if refusal is not None or meter is not MeterCondition.FRESH:
        return StartDecision(load=load.slug, refusal=refusal, overrun=None)

    excess = overrun(load.phase, load.amps, currents, thresholds.start_ceiling)
    return StartDecision(
        load=load.slug,
        refusal=None if excess is None else StartRefusal.NO_HEADROOM,
        overrun=excess,
    )


def in_start_order(loads: Iterable[LoadStatus]) -> tuple[LoadStatus, ...]:
    return tuple(sorted(loads, key=lambda load: load.priority, reverse=True))


def _refusal(
    load: LoadStatus,
    meter: MeterCondition,
    loads: Sequence[LoadStatus],
    last_permit_at: float | None,
    at: float,
    thresholds: PhaseThresholds,
) -> StartRefusal | None:
    if meter is MeterCondition.STALE or (meter is MeterCondition.LOST and load.kind is LoadKind.PLUG):
        return StartRefusal.METER_STALE

    if meter is MeterCondition.LOST and any(
        floor.slug != load.slug and floor.phase is load.phase for floor in drawing_floors(loads)
    ):
        return StartRefusal.PHASE_TAKEN

    if not elapsed(last_permit_at, at, thresholds.start_interval):
        return StartRefusal.TOO_SOON

    return None
