from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from apps.climate.domain.air_plan import AirPlan, plan_air
from apps.climate.domain.air_policy import resolve_control_path
from apps.climate.domain.control import ClimateControl
from apps.climate.domain.events import AirControlDryRun, OptimalStartDryRun
from apps.climate.domain.floor_policy import ThermostatTarget, decide_floor_target
from apps.climate.domain.learning import Morning, ZoneLearningRecord
from apps.climate.domain.memory import AirDecision, ZoneMemory
from apps.climate.domain.modes import FOLLOWED, AirState, ControlPath, ScheduleMode, ZoneCycleOutcome, ZoneKind
from apps.climate.domain.observation import (
    MorningProgress,
    MorningReading,
    StopProgress,
    StopReading,
    StopWatch,
    observe_morning,
    observe_stop,
    start_stop_watch,
)
from apps.climate.domain.schedule import resolve_mode
from apps.climate.domain.second_floor_guard import apply_second_floor_guard
from apps.climate.domain.write_guard import write_allowed
from apps.climate.domain.zone import ZoneSettings

MINUTE = timedelta(minutes=1)


@dataclass(frozen=True, kw_only=True)
class CycleConditions:
    now: datetime
    clock_trusted: bool
    control: ClimateControl
    outdoor: Decimal | None


@dataclass(frozen=True, kw_only=True)
class ZoneConditions:
    air: Decimal | None
    learning: ZoneLearningRecord
    guarded: bool
    writes_live: bool


@dataclass(frozen=True, kw_only=True)
class ZoneDecision:
    path: ControlPath
    plan: AirPlan | None
    mode: ScheduleMode
    target: ThermostatTarget


@dataclass(frozen=True, kw_only=True)
class ZoneFollowUp:
    memory: ZoneMemory
    air_dry_run: AirControlDryRun | None
    optimal_start: OptimalStartDryRun | None
    morning: Morning | None
    stop: StopWatch | None


def decide_zone(
    zone: ZoneSettings,
    memory: ZoneMemory,
    conditions: ZoneConditions,
    cycle: CycleConditions,
) -> ZoneDecision:
    path = resolve_control_path(zone, air_available=conditions.air is not None)
    plan = None if conditions.air is None else _plan(zone, memory, path, conditions.air, conditions, cycle)
    if plan is not None and path is ControlPath.AIR:
        mode, target = plan.mode, plan.thermostat
    else:
        mode = resolve_mode(zone.windows, cycle.now, zone.preheat_lead)
        target = decide_floor_target(zone, mode, cycle.now, clock_trusted=cycle.clock_trusted)
    return ZoneDecision(path=path, plan=plan, mode=mode, target=_guarded(zone, target, conditions))


def follow_zone(
    zone: ZoneSettings,
    memory: ZoneMemory,
    decision: ZoneDecision,
    outcome: ZoneCycleOutcome,
    conditions: ZoneConditions,
    cycle: CycleConditions,
) -> ZoneFollowUp:
    morning = _observe_morning(zone, memory, decision, outcome, conditions, cycle)
    follow_up = ZoneFollowUp(
        memory=ZoneMemory(
            path=decision.path,
            mode=decision.mode if cycle.clock_trusted else None,
            would_be=memory.would_be,
            preheat=memory.preheat if decision.plan is None else decision.plan.preheat,
            morning=morning.morning,
        ),
        air_dry_run=None,
        optimal_start=None,
        morning=morning.finished,
        stop=None,
    )
    if decision.plan is None:
        return follow_up

    if decision.path is ControlPath.AIR:
        return _follow_air(follow_up, zone, memory, decision, decision.plan, outcome, conditions, cycle)

    return _follow_air_dry_run(follow_up, zone, memory, decision.plan, outcome, conditions, cycle)


def _plan(
    zone: ZoneSettings,
    memory: ZoneMemory,
    path: ControlPath,
    air: Decimal,
    conditions: ZoneConditions,
    cycle: CycleConditions,
) -> AirPlan | None:
    if path is ControlPath.FLOOR:
        return None

    return plan_air(
        zone,
        memory.air_state_on(path),
        memory.preheat,
        air=air,
        outdoor=cycle.outdoor,
        learning=conditions.learning,
        control=cycle.control,
        now=cycle.now,
        clock_trusted=cycle.clock_trusted,
        dry_run=path is ControlPath.AIR_DRY_RUN,
    )


def _guarded(zone: ZoneSettings, target: ThermostatTarget, conditions: ZoneConditions) -> ThermostatTarget:
    return apply_second_floor_guard(target, zone.setpoints) if conditions.guarded else target


def _follow_air(
    follow_up: ZoneFollowUp,
    zone: ZoneSettings,
    memory: ZoneMemory,
    decision: ZoneDecision,
    plan: AirPlan,
    outcome: ZoneCycleOutcome,
    conditions: ZoneConditions,
    cycle: CycleConditions,
) -> ZoneFollowUp:
    current = memory.air_state_on(ControlPath.AIR)
    heat_target = memory.heat_target_on(ControlPath.AIR)
    state = _followed_air_state(memory, decision, outcome)
    if memory.stop is None:
        idle = ThermostatTarget(power=True, setpoint=zone.setpoints.idle)
        watch = start_stop_watch(
            current,
            state,
            applied=outcome is ZoneCycleOutcome.APPLIED and cycle.clock_trusted and decision.target == idle,
            heat_target=heat_target,
            target=plan.target,
            air=plan.air,
            at=cycle.now,
        )
        stop = StopProgress(watch=watch, finished=None)
    else:
        stop = observe_stop(
            memory.stop,
            StopReading(
                at=cycle.now,
                air=plan.air,
                watching=conditions.writes_live and cycle.clock_trusted and not zone.is_boosted(cycle.now),
                heating=state is AirState.HEAT,
            ),
        )
    return replace(
        follow_up,
        memory=replace(
            follow_up.memory,
            air_state=state,
            heat_target=plan.target if plan.state is AirState.HEAT else heat_target,
            stop=stop.watch,
        ),
        stop=stop.finished,
    )


def _follow_air_dry_run(
    follow_up: ZoneFollowUp,
    zone: ZoneSettings,
    memory: ZoneMemory,
    plan: AirPlan,
    outcome: ZoneCycleOutcome,
    conditions: ZoneConditions,
    cycle: CycleConditions,
) -> ZoneFollowUp:
    follow_up = replace(follow_up, optimal_start=_optimal_start(zone, memory, plan, cycle.now))
    would_be = AirDecision(state=plan.state, target=_guarded(zone, plan.thermostat, conditions))
    if would_be == memory.would_be:
        return replace(follow_up, memory=replace(follow_up.memory, air_state=plan.state))

    if outcome is ZoneCycleOutcome.DEVICE_UNAVAILABLE or not write_allowed(zone.last_air_dry_run_at, cycle.now):
        return replace(
            follow_up,
            memory=replace(follow_up.memory, air_state=memory.air_state_on(ControlPath.AIR_DRY_RUN)),
        )

    return replace(
        follow_up,
        memory=replace(follow_up.memory, air_state=plan.state, would_be=would_be),
        air_dry_run=AirControlDryRun(
            zone=zone.slug,
            air=plan.air,
            target=plan.target,
            mode=plan.mode,
            state=plan.state,
            power=would_be.target.power,
            setpoint=would_be.target.setpoint,
            early_stop_offset=plan.early_stop_offset,
            lead_minutes=None if plan.lead is None else plan.lead // MINUTE,
            occurred_at=cycle.now,
        ),
    )


def _optimal_start(zone: ZoneSettings, memory: ZoneMemory, plan: AirPlan, now: datetime) -> OptimalStartDryRun | None:
    preheat = plan.preheat
    if preheat is None or preheat == memory.preheat:
        return None

    return OptimalStartDryRun(
        zone=zone.slug,
        lead_minutes=preheat.lead // MINUTE,
        start=preheat.start,
        fixed_start=preheat.comfort_at - zone.preheat_lead,
        occurred_at=now,
    )


def _observe_morning(
    zone: ZoneSettings,
    memory: ZoneMemory,
    decision: ZoneDecision,
    outcome: ZoneCycleOutcome,
    conditions: ZoneConditions,
    cycle: CycleConditions,
) -> MorningProgress:
    if zone.kind is not ZoneKind.AIR or zone.air_sensor_slug is None:
        return MorningProgress(morning=None, finished=None)

    return observe_morning(
        memory.morning,
        MorningReading(
            at=cycle.now,
            mode=decision.mode,
            previous_mode=memory.mode,
            windows=zone.windows,
            target=zone.air_targets.comfort,
            air=conditions.air,
            outdoor=cycle.outdoor,
            heating=(
                conditions.writes_live
                and zone.heating_enabled
                and cycle.clock_trusted
                and not zone.is_boosted(cycle.now)
            ),
            outcome=outcome,
            air_state=_followed_air_state(memory, decision, outcome),
        ),
    )


def _followed_air_state(memory: ZoneMemory, decision: ZoneDecision, outcome: ZoneCycleOutcome) -> AirState | None:
    if decision.path is not ControlPath.AIR or decision.plan is None:
        return None

    return decision.plan.state if outcome in FOLLOWED else memory.air_state_on(ControlPath.AIR)
