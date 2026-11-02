from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.loads import LoadPhase
from apps.power.domain.moments import elapsed, held, held_since

ALARM_AFTER = timedelta(minutes=1)


@dataclass(frozen=True, kw_only=True)
class PhaseWatch:
    above_since: float | None = None
    calm_since: float | None = None
    overloaded: bool = False
    measured_above_since: float | None = None
    measured_calm_since: float | None = None
    alarmed: bool = False
    alarm_since: float | None = None
    alarm_amps: Decimal | None = None
    last_shed_at: float | None = None


@dataclass(frozen=True, kw_only=True)
class PhaseWatchStep:
    watch: PhaseWatch
    overloaded: bool
    calmed: bool
    alarmed: bool
    alarm_cleared: bool


def watch_phase(
    watch: PhaseWatch,
    measured: Decimal | None,
    relief: Decimal,
    at: float,
    thresholds: PhaseThresholds,
) -> PhaseWatchStep:
    if measured is None:
        unheld = replace(
            watch,
            above_since=None,
            calm_since=None,
            measured_above_since=None,
            measured_calm_since=None,
        )
        return PhaseWatchStep(watch=unheld, overloaded=False, calmed=False, alarmed=False, alarm_cleared=False)

    current = measured - relief
    above_since = held_since(watch.above_since, current > thresholds.shed, at)
    calm_since = held_since(watch.calm_since, current <= thresholds.restore, at)
    measured_above_since = held_since(watch.measured_above_since, measured > thresholds.shed, at)
    measured_calm_since = held_since(watch.measured_calm_since, measured <= thresholds.restore, at)
    overloaded = _in_episode(
        watch.overloaded,
        held(above_since, at, thresholds.shed_hold),
        held(calm_since, at, thresholds.restore_hold),
    )
    alarmed = _in_episode(
        watch.alarmed,
        held(measured_above_since, at, ALARM_AFTER),
        held(measured_calm_since, at, thresholds.restore_hold),
    )
    raised = alarmed and not watch.alarmed
    return PhaseWatchStep(
        watch=replace(
            watch,
            above_since=above_since,
            calm_since=calm_since,
            overloaded=overloaded,
            measured_above_since=measured_above_since,
            measured_calm_since=measured_calm_since,
            alarmed=alarmed,
            alarm_since=_kept(alarmed, raised, measured_above_since, watch.alarm_since),
            alarm_amps=_kept(alarmed, raised, measured, watch.alarm_amps),
        ),
        overloaded=overloaded and not watch.overloaded,
        calmed=watch.overloaded and not overloaded,
        alarmed=raised,
        alarm_cleared=watch.alarmed and not alarmed,
    )


def shed_due(
    watch: PhaseWatch,
    measured: Decimal | None,
    relief: Decimal,
    at: float,
    thresholds: PhaseThresholds,
) -> bool:
    return (
        measured is not None
        and watch.overloaded
        and measured - relief > thresholds.start_ceiling
        and elapsed(watch.last_shed_at, at, thresholds.shed_interval)
    )


def with_shed(watch: PhaseWatch, at: float) -> PhaseWatch:
    return replace(watch, last_shed_at=at)


def calm_phases(
    watches: Mapping[LoadPhase, PhaseWatch],
    at: float,
    thresholds: PhaseThresholds,
) -> frozenset[LoadPhase]:
    return frozenset(phase for phase, watch in watches.items() if held(watch.calm_since, at, thresholds.restore_hold))


def _in_episode(active: bool, entered: bool, left: bool) -> bool:
    return not left if active else entered


def _kept[TValue](active: bool, raised: bool, value: TValue, kept: TValue | None) -> TValue | None:
    if not active:
        return None

    return value if raised else kept
