from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.air_policy import resolve_air_target
from apps.climate.domain.floor_policy import ThermostatTarget, decide_floor_target
from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.schedule import resolve_mode
from apps.climate.domain.zone import ZoneSettings


@dataclass(frozen=True, kw_only=True)
class ZoneIntent:
    target: Decimal | None
    backup: bool
    learns: bool


NO_INTENT = ZoneIntent(target=None, backup=False, learns=True)


@dataclass(frozen=True, kw_only=True)
class FloorDecision:
    mode: ScheduleMode
    target: ThermostatTarget


def decide_floor_mode(
    zone: ZoneSettings,
    forced: Decimal | None,
    now: datetime,
    *,
    clock_trusted: bool,
) -> FloorDecision:
    mode = resolve_mode(zone.windows, now, zone.preheat_lead)
    target = decide_floor_target(zone, mode, now, clock_trusted=clock_trusted)
    if forced is None or not target.power or zone.is_boosted(now):
        return FloorDecision(mode=mode, target=target)

    held = mode if clock_trusted else ScheduleMode.COMFORT
    scheduled = resolve_air_target(zone, held, clock_trusted=clock_trusted)
    if forced < scheduled:
        return FloorDecision(mode=ScheduleMode.NIGHT, target=_setpoint(zone.setpoints.night))

    if forced == scheduled:
        return FloorDecision(mode=held, target=target)

    if held is ScheduleMode.NIGHT:
        return FloorDecision(mode=ScheduleMode.COMFORT, target=_setpoint(zone.setpoints.comfort))

    return FloorDecision(mode=held, target=_setpoint(zone.setpoints.boost))


def forced_air_mode(zone: ZoneSettings, forced: Decimal) -> ScheduleMode:
    return ScheduleMode.COMFORT if forced > zone.air_targets.night else ScheduleMode.NIGHT


def _setpoint(setpoint: Decimal) -> ThermostatTarget:
    return ThermostatTarget(power=True, setpoint=setpoint)
