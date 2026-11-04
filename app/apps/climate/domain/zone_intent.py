from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.air_policy import resolve_air_target
from apps.climate.domain.floor_policy import ThermostatTarget, decide_floor_target
from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.schedule import resolve_mode
from apps.climate.domain.zone import ZoneSettings


@dataclass(frozen=True, kw_only=True)
class ForcedTarget:
    target: Decimal
    beneath: Decimal

    def raises(self, own: Decimal) -> bool:
        return self.target > max(own, self.beneath)

    def lowers(self, own: Decimal) -> bool:
        return self.target < min(own, self.beneath)


@dataclass(frozen=True, kw_only=True)
class ZoneIntent:
    forced: tuple[ForcedTarget, ...]
    backup: bool
    learns: bool
    hands_off: bool = False


NO_INTENT = ZoneIntent(forced=(), backup=False, learns=True)


@dataclass(frozen=True, kw_only=True)
class FloorDecision:
    mode: ScheduleMode
    target: ThermostatTarget


def decide_floor_mode(
    zone: ZoneSettings,
    forced: Sequence[ForcedTarget],
    now: datetime,
    *,
    clock_trusted: bool,
) -> FloorDecision:
    mode = resolve_mode(zone.windows, now, zone.preheat_lead)
    target = decide_floor_target(zone, mode, now, clock_trusted=clock_trusted)
    if not forced or not target.power or zone.is_boosted(now):
        return FloorDecision(mode=mode, target=target)

    held = mode if clock_trusted else ScheduleMode.COMFORT
    decision = FloorDecision(mode=held, target=target)
    air = resolve_air_target(zone, held, clock_trusted=clock_trusted)
    for layer in forced:
        if layer.raises(air):
            decision, air = _raised(zone, decision), layer.target
        elif layer.lowers(air):
            decision, air = _night(zone), layer.target
    return decision


def forced_air_target(own: Decimal, forced: Sequence[ForcedTarget]) -> Decimal:
    target = own
    for layer in forced:
        if layer.raises(target) or layer.lowers(target):
            target = layer.target
    return target


def forced_air_mode(zone: ZoneSettings, forced: Decimal) -> ScheduleMode:
    return ScheduleMode.COMFORT if forced > zone.air_targets.night else ScheduleMode.NIGHT


def _raised(zone: ZoneSettings, decision: FloorDecision) -> FloorDecision:
    if decision.mode is ScheduleMode.NIGHT:
        return FloorDecision(mode=ScheduleMode.COMFORT, target=_setpoint(zone.setpoints.comfort))

    return FloorDecision(mode=decision.mode, target=_setpoint(zone.setpoints.boost))


def _night(zone: ZoneSettings) -> FloorDecision:
    return FloorDecision(mode=ScheduleMode.NIGHT, target=_setpoint(zone.setpoints.night))


def _setpoint(setpoint: Decimal) -> ThermostatTarget:
    return ThermostatTarget(power=True, setpoint=setpoint)
