from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from apps.climate.domain.air_plan import AirPlan, plan_air
from apps.climate.domain.air_policy import resolve_control_path
from apps.climate.domain.backup_heat import apply_backup_heat
from apps.climate.domain.control import ClimateControl
from apps.climate.domain.events import AirControlDryRun, OptimalStartDryRun
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.learning import Morning, ZoneLearningRecord
from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import FOLLOWED, AirState, ControlPath, ScheduleMode, ZoneCycleOutcome, ZoneKind
from apps.climate.domain.observation import (
    MorningProgress,
    MorningReading,
    StopProgress,
    StopReading,
    StopWatch,
    note_preheat,
    observe_morning,
    observe_stop,
    start_stop_watch,
)
from apps.climate.domain.write_guard import write_allowed
from apps.climate.domain.zone import ZoneSettings
from apps.climate.domain.zone_intent import ZoneIntent, decide_floor_mode, forced_air_mode

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
    intent: ZoneIntent
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
    forced = conditions.intent.forced
    if plan is not None and path is ControlPath.AIR:
        mode = forced_air_mode(zone, plan.target) if forced else plan.mode
        target = plan.thermostat
    else:
        floor = decide_floor_mode(zone, forced, cycle.now, clock_trusted=cycle.clock_trusted)
        mode, target = floor.mode, floor.target
    return ZoneDecision(path=path, plan=plan, mode=mode, target=_backed_up(zone, target, conditions))


def follow_zone(
    zone: ZoneSettings,
    memory: ZoneMemory,
    decision: ZoneDecision,
    outcome: ZoneCycleOutcome,
    conditions: ZoneConditions,
    cycle: CycleConditions,
) -> ZoneFollowUp:
    reading = _morning_reading(zone, memory, decision, outcome, conditions, cycle)
    kept = _kept_memory(zone, memory, decision, outcome, reading, cycle)
    if outcome is ZoneCycleOutcome.SHED:
        return ZoneFollowUp(
            memory=kept,
            air_dry_run=None,
            optimal_start=None,
            morning=None,
            stop=None,
        )

    morning = _observe_morning(memory.morning, reading)
    follow_up = ZoneFollowUp(
        memory=replace(kept, morning=morning.morning),
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


def hold_air_dry_run(follow_up: ZoneFollowUp, memory: ZoneMemory) -> ZoneFollowUp:
    return replace(
        follow_up,
        memory=replace(
            follow_up.memory,
            air_state=memory.air_state_on(ControlPath.AIR_DRY_RUN),
            would_be_target=memory.would_be_target,
        ),
        air_dry_run=None,
    )


def _kept_memory(
    zone: ZoneSettings,
    memory: ZoneMemory,
    decision: ZoneDecision,
    outcome: ZoneCycleOutcome,
    reading: MorningReading | None,
    cycle: CycleConditions,
) -> ZoneMemory:
    return ZoneMemory(
        path=decision.path,
        mode=_mode_followed(memory, decision, outcome, cycle),
        would_be_target=_would_be_target(zone, memory, decision, outcome),
        preheat=memory.preheat if decision.plan is None else decision.plan.preheat,
        preheated_for=None if reading is None else note_preheat(reading),
    )


def _mode_followed(
    memory: ZoneMemory,
    decision: ZoneDecision,
    outcome: ZoneCycleOutcome,
    cycle: CycleConditions,
) -> ScheduleMode | None:
    if not cycle.clock_trusted:
        return None

    if decision.mode is ScheduleMode.NIGHT and outcome not in FOLLOWED:
        return memory.mode

    return decision.mode


def _would_be_target(
    zone: ZoneSettings,
    memory: ZoneMemory,
    decision: ZoneDecision,
    outcome: ZoneCycleOutcome,
) -> ThermostatTarget | None:
    if decision.path is ControlPath.AIR_DRY_RUN:
        return memory.would_be_target

    if not _has_air_sensor(zone) or outcome not in FOLLOWED:
        return None

    return decision.target


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
        forced=conditions.intent.forced,
    )


def _backed_up(zone: ZoneSettings, target: ThermostatTarget, conditions: ZoneConditions) -> ThermostatTarget:
    return apply_backup_heat(target, zone.setpoints) if conditions.intent.backup else target


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
            applied=(
                outcome is ZoneCycleOutcome.APPLIED
                and cycle.clock_trusted
                and decision.target == idle
                and conditions.intent.learns
            ),
            heat_target=heat_target,
            target=plan.target,
            offset=plan.early_stop_offset,
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
                watching=_learns(zone, conditions, cycle),
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
    target = _backed_up(zone, plan.thermostat, conditions)
    if outcome is ZoneCycleOutcome.DEVICE_UNAVAILABLE:
        return hold_air_dry_run(follow_up, memory)

    if target == memory.would_be_target:
        return replace(follow_up, memory=replace(follow_up.memory, air_state=plan.state))

    if not write_allowed(zone.last_air_dry_run_at, cycle.now):
        return hold_air_dry_run(follow_up, memory)

    return replace(
        follow_up,
        memory=replace(follow_up.memory, air_state=plan.state, would_be_target=target),
        air_dry_run=AirControlDryRun(
            zone=zone.slug,
            air=plan.air,
            target=plan.target,
            mode=plan.mode,
            state=plan.state,
            previous_state=memory.air_state_on(ControlPath.AIR_DRY_RUN),
            power=target.power,
            setpoint=target.setpoint,
            start_offset=cycle.control.regulation.start_offset,
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


def _morning_reading(
    zone: ZoneSettings,
    memory: ZoneMemory,
    decision: ZoneDecision,
    outcome: ZoneCycleOutcome,
    conditions: ZoneConditions,
    cycle: CycleConditions,
) -> MorningReading | None:
    if not _has_air_sensor(zone):
        return None

    return MorningReading(
        at=cycle.now,
        mode=decision.mode if cycle.clock_trusted else None,
        previous_mode=memory.mode,
        entering_air_mode=decision.path is ControlPath.AIR and memory.path is not ControlPath.AIR,
        preheated_for=memory.preheated_for,
        windows=zone.windows,
        target=zone.air_targets.comfort,
        air=conditions.air,
        outdoor=cycle.outdoor,
        heating=zone.heating_enabled and _learns(zone, conditions, cycle),
        outcome=outcome,
        air_state=_followed_air_state(memory, decision, outcome),
    )


def _learns(zone: ZoneSettings, conditions: ZoneConditions, cycle: CycleConditions) -> bool:
    return (
        conditions.writes_live and conditions.intent.learns and cycle.clock_trusted and not zone.is_boosted(cycle.now)
    )


def _has_air_sensor(zone: ZoneSettings) -> bool:
    return zone.kind is ZoneKind.AIR and zone.air_sensor_slug is not None


def _observe_morning(morning: Morning | None, reading: MorningReading | None) -> MorningProgress:
    if reading is None:
        return MorningProgress(morning=None, finished=None)

    return observe_morning(morning, reading)


def _followed_air_state(memory: ZoneMemory, decision: ZoneDecision, outcome: ZoneCycleOutcome) -> AirState | None:
    if decision.path is not ControlPath.AIR or decision.plan is None:
        return None

    return decision.plan.state if outcome in FOLLOWED else memory.air_state_on(ControlPath.AIR)
