from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode, ZoneKind
from apps.climate.domain.zone import ZoneSettings


@dataclass(frozen=True, kw_only=True)
class AirRegulationSettings:
    start_offset: Decimal
    initial_early_stop_offset: Decimal
    min_band: Decimal

    def early_stop_offset(self, learned: Decimal | None) -> Decimal:
        offset = self.initial_early_stop_offset if learned is None else learned
        return min(offset, self.start_offset - self.min_band)


def resolve_control_path(zone: ZoneSettings, *, air_available: bool) -> ControlPath:
    if zone.kind is not ZoneKind.AIR or zone.air_sensor_slug is None or not air_available:
        return ControlPath.FLOOR

    return ControlPath.AIR if zone.air_control_live else ControlPath.AIR_DRY_RUN


def resolve_air_target(zone: ZoneSettings, mode: ScheduleMode, *, clock_trusted: bool) -> Decimal:
    if not clock_trusted or mode is not ScheduleMode.NIGHT:
        return zone.air_targets.comfort

    return zone.air_targets.night


def decide_air_state(
    current: AirState | None,
    air: Decimal,
    target: Decimal,
    *,
    start_offset: Decimal,
    early_stop_offset: Decimal,
) -> AirState:
    if current is AirState.HEAT:
        return AirState.IDLE if air >= target - early_stop_offset else AirState.HEAT

    return AirState.HEAT if air < target - start_offset else AirState.IDLE


def decide_air_actuation(zone: ZoneSettings, state: AirState, now: datetime) -> ThermostatTarget:
    if not zone.heating_enabled:
        return ThermostatTarget(power=False, setpoint=None)

    if zone.is_boosted(now):
        return ThermostatTarget(power=True, setpoint=zone.setpoints.boost)

    if state is AirState.HEAT:
        return ThermostatTarget(power=True, setpoint=zone.setpoints.limit)

    return ThermostatTarget(power=True, setpoint=zone.setpoints.idle)
