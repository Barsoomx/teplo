from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.headroom import PhaseOverrun, overrun, phases_of
from apps.power.domain.load_status import LoadStatus
from apps.power.domain.loads import LoadKind
from apps.power.domain.meter_watch import MeterCondition, drawing_floors, silent_phase_allows
from apps.power.domain.moments import elapsed
from apps.power.domain.shedding import Shed
from apps.power.domain.view import GuardView


class StartRefusal(StrEnum):
    METER_STALE = 'meter_stale'
    PHASE_TAKEN = 'phase_taken'
    TOO_SOON = 'too_soon'
    NO_HEADROOM = 'no_headroom'
    PHASE_OVERLOADED = 'phase_overloaded'
    PHASE_NOT_CALM = 'phase_not_calm'
    PHASE_WOULD_OVERLOAD = 'phase_would_overload'


@dataclass(frozen=True, kw_only=True)
class StartDecision:
    load: str
    refusal: StartRefusal | None
    overrun: PhaseOverrun | None

    @property
    def granted(self) -> bool:
        return self.refusal is None


@dataclass(frozen=True, kw_only=True)
class StartRequest:
    load: LoadStatus
    live: bool
    handed_back: Shed | None = None


@dataclass(frozen=True, kw_only=True)
class StartRound:
    decisions: Mapping[str, StartDecision]
    last_permit_at: float | None


def decide_start(
    load: LoadStatus,
    *,
    meter: MeterCondition,
    view: GuardView,
    last_permit_at: float | None,
    at: float,
    thresholds: PhaseThresholds,
    handed_back: Shed | None = None,
) -> StartDecision:
    refusal = _refusal(load, meter, view.loads, last_permit_at, at, thresholds)
    if refusal is not None:
        return StartDecision(load=load.slug, refusal=refusal, overrun=None)

    excess = overrun(load.phase, load.amps, view.currents, thresholds.start_ceiling)
    if excess is not None:
        return StartDecision(load=load.slug, refusal=StartRefusal.NO_HEADROOM, overrun=excess)

    refusal = _silent_refusal(load, view, handed_back)
    if refusal is not None:
        return StartDecision(load=load.slug, refusal=refusal, overrun=None)

    excess = overrun(load.phase, load.amps, view.silent_currents, thresholds.shed)
    if excess is not None:
        return StartDecision(load=load.slug, refusal=StartRefusal.PHASE_WOULD_OVERLOAD, overrun=excess)

    return StartDecision(load=load.slug, refusal=None, overrun=None)


def decide_starts(
    requests: Sequence[StartRequest],
    *,
    meter: MeterCondition,
    live_view: GuardView,
    dry_run_view: GuardView,
    last_permit_at: float | None,
    at: float,
    thresholds: PhaseThresholds,
) -> StartRound:
    live = {request.load.slug for request in requests if request.live}
    handed_back = {request.load.slug: request.handed_back for request in requests}
    permit_at = last_permit_at
    would_be_permit_at: float | None = None
    decisions: dict[str, StartDecision] = {}
    for load in in_start_order(request.load for request in requests):
        decision = decide_start(
            load,
            meter=meter,
            view=live_view if load.slug in live else dry_run_view,
            last_permit_at=permit_at if load.slug in live else _latest(permit_at, would_be_permit_at),
            at=at,
            thresholds=thresholds,
            handed_back=handed_back[load.slug],
        )
        decisions[load.slug] = decision
        if decision.granted and load.slug in live:
            permit_at = at
        elif decision.granted:
            would_be_permit_at = at
    return StartRound(decisions=decisions, last_permit_at=permit_at)


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
    if meter is MeterCondition.STALE or (meter is MeterCondition.LOST and load.kind is not LoadKind.ZONE):
        return StartRefusal.METER_STALE

    if meter is MeterCondition.LOST and any(
        floor.slug != load.slug and floor.phase is load.phase for floor in drawing_floors(loads)
    ):
        return StartRefusal.PHASE_TAKEN

    if not elapsed(last_permit_at, at, thresholds.start_interval):
        return StartRefusal.TOO_SOON

    return None


def _silent_refusal(load: LoadStatus, view: GuardView, handed_back: Shed | None) -> StartRefusal | None:
    if any(phase not in view.currents and phase in view.overloaded for phase in phases_of(load.phase)):
        return StartRefusal.PHASE_OVERLOADED

    if handed_back is None or handed_back.phase in view.currents:
        return None

    allowed = silent_phase_allows(
        handed_back.phase,
        shed_for=handed_back.reason,
        calming=view.calming,
        overloaded=view.overloaded,
    )
    return None if allowed else StartRefusal.PHASE_NOT_CALM


def _latest(first: float | None, second: float | None) -> float | None:
    return max((moment for moment in (first, second) if moment is not None), default=None)
