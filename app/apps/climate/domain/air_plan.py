from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from apps.climate.domain.air_policy import decide_air_actuation, decide_air_state, resolve_air_target
from apps.climate.domain.control import ClimateControl
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.learning import ZoneLearningRecord
from apps.climate.domain.modes import AirState, ScheduleMode
from apps.climate.domain.optimal_start import NO_LEAD, PreheatStart, latch_preheat, optimal_lead
from apps.climate.domain.schedule import next_comfort_start, resolve_mode
from apps.climate.domain.zone import ZoneSettings


@dataclass(frozen=True, kw_only=True)
class AirPlan:
    air: Decimal
    preheat: PreheatStart | None
    schedule_lead: timedelta
    mode: ScheduleMode
    target: Decimal
    early_stop_offset: Decimal
    state: AirState
    thermostat: ThermostatTarget

    @property
    def lead(self) -> timedelta | None:
        if self.mode is not ScheduleMode.PREHEAT or self.preheat is None:
            return None

        return self.preheat.lead


def plan_air(
    zone: ZoneSettings,
    current: AirState | None,
    latched: PreheatStart | None,
    *,
    air: Decimal,
    outdoor: Decimal | None,
    learning: ZoneLearningRecord,
    control: ClimateControl,
    now: datetime,
    clock_trusted: bool,
    dry_run: bool,
) -> AirPlan:
    lead = optimal_lead(
        learning.heat_up,
        air=air,
        outdoor=outdoor,
        target=zone.air_targets.comfort,
        fixed_lead=zone.preheat_lead,
        settings=control.optimal_start,
    )
    preheat = (
        latch_preheat(
            latched,
            comfort_at=next_comfort_start(zone.windows, now),
            lead=lead,
            now=now,
            latch_by=zone.preheat_lead if dry_run else NO_LEAD,
        )
        if clock_trusted
        else latched
    )
    schedule_lead = lead if preheat is None else preheat.lead
    mode = resolve_mode(zone.windows, now, schedule_lead)
    target = resolve_air_target(zone, mode, clock_trusted=clock_trusted)
    early_stop_offset = control.regulation.early_stop_offset(learning.early_stop_offset)
    state = decide_air_state(
        current,
        air,
        target,
        start_offset=control.regulation.start_offset,
        early_stop_offset=early_stop_offset,
    )
    return AirPlan(
        air=air,
        preheat=preheat,
        schedule_lead=schedule_lead,
        mode=mode,
        target=target,
        early_stop_offset=early_stop_offset,
        state=state,
        thermostat=decide_air_actuation(zone, state, now),
    )
