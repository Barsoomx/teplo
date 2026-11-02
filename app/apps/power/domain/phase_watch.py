from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal

from apps.power.domain.control import PhaseThresholds
from apps.power.domain.loads import LoadPhase
from apps.power.domain.moments import elapsed, held, held_since

ALARM_AFTER = timedelta(minutes=1)


@dataclass(frozen=True, kw_only=True)
class PhaseAmps:
    measured: Decimal
    live: Decimal
    dry_run: Decimal


@dataclass(frozen=True, kw_only=True)
class OverloadWatch:
    above_since: float | None = None
    calm_since: float | None = None
    overloaded: bool = False
    last_shed_at: float | None = None


@dataclass(frozen=True, kw_only=True)
class PhaseWatch:
    live: OverloadWatch = OverloadWatch()
    dry_run: OverloadWatch = OverloadWatch()
    measured_above_since: float | None = None
    measured_calm_since: float | None = None
    alarmed: bool = False
    alarm_since: float | None = None
    alarm_amps: Decimal | None = None

    def overload(self, *, live: bool) -> OverloadWatch:
        return self.live if live else self.dry_run


@dataclass(frozen=True, kw_only=True)
class PhaseWatchStep:
    watch: PhaseWatch
    overloaded: bool
    calmed: bool
    alarmed: bool
    alarm_cleared: bool


def watch_phase(
    watch: PhaseWatch,
    amps: PhaseAmps | None,
    at: float,
    thresholds: PhaseThresholds,
) -> PhaseWatchStep:
    if amps is None:
        unheld = replace(
            watch,
            live=_unheld(watch.live),
            dry_run=_unheld(watch.dry_run),
            measured_above_since=None,
            measured_calm_since=None,
        )
        return PhaseWatchStep(watch=unheld, overloaded=False, calmed=False, alarmed=False, alarm_cleared=False)

    live = _followed(watch.live, amps.live, at, thresholds)
    measured_above_since = held_since(watch.measured_above_since, amps.measured > thresholds.shed, at)
    measured_calm_since = held_since(watch.measured_calm_since, amps.measured <= thresholds.restore, at)
    alarmed = _in_episode(
        watch.alarmed,
        held(measured_above_since, at, ALARM_AFTER),
        held(measured_calm_since, at, thresholds.restore_hold),
    )
    raised = alarmed and not watch.alarmed
    return PhaseWatchStep(
        watch=replace(
            watch,
            live=live,
            dry_run=_followed(watch.dry_run, amps.dry_run, at, thresholds),
            measured_above_since=measured_above_since,
            measured_calm_since=measured_calm_since,
            alarmed=alarmed,
            alarm_since=_kept(alarmed, raised, measured_above_since, watch.alarm_since),
            alarm_amps=_kept(alarmed, raised, amps.measured, watch.alarm_amps),
        ),
        overloaded=live.overloaded and not watch.live.overloaded,
        calmed=watch.live.overloaded and not live.overloaded,
        alarmed=raised,
        alarm_cleared=watch.alarmed and not alarmed,
    )


def shed_due(
    watch: OverloadWatch,
    current: Decimal | None,
    at: float,
    thresholds: PhaseThresholds,
) -> bool:
    return (
        current is not None
        and watch.overloaded
        and current > thresholds.start_ceiling
        and elapsed(watch.last_shed_at, at, thresholds.shed_interval)
    )


def with_shed(watch: PhaseWatch, at: float, *, live: bool) -> PhaseWatch:
    if live:
        return replace(watch, live=replace(watch.live, last_shed_at=at))

    return replace(watch, dry_run=replace(watch.dry_run, last_shed_at=at))


def calm_phases(
    watches: Mapping[LoadPhase, PhaseWatch],
    at: float,
    thresholds: PhaseThresholds,
    *,
    live: bool,
) -> frozenset[LoadPhase]:
    return frozenset(
        phase
        for phase, watch in watches.items()
        if held(watch.overload(live=live).calm_since, at, thresholds.restore_hold)
    )


def _followed(watch: OverloadWatch, current: Decimal, at: float, thresholds: PhaseThresholds) -> OverloadWatch:
    above_since = held_since(watch.above_since, current > thresholds.shed, at)
    calm_since = held_since(watch.calm_since, current <= thresholds.restore, at)
    return replace(
        watch,
        above_since=above_since,
        calm_since=calm_since,
        overloaded=_in_episode(
            watch.overloaded,
            held(above_since, at, thresholds.shed_hold),
            held(calm_since, at, thresholds.restore_hold),
        ),
    )


def _unheld(watch: OverloadWatch) -> OverloadWatch:
    return replace(watch, above_since=None, calm_since=None)


def _in_episode(active: bool, entered: bool, left: bool) -> bool:
    return not left if active else entered


def _kept[TValue](active: bool, raised: bool, value: TValue, kept: TValue | None) -> TValue | None:
    if not active:
        return None

    return value if raised else kept
