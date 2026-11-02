from dataclasses import dataclass
from decimal import Decimal

from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.zone import FloorSetpoints


@dataclass(frozen=True, kw_only=True)
class SecondFloorGuardSettings:
    zone_slug: str | None
    threshold: Decimal
    hysteresis: Decimal
    live: bool


def decide_second_floor_guard(active: bool, air: Decimal | None, settings: SecondFloorGuardSettings) -> bool:
    if air is None or settings.zone_slug is None:
        return False

    if air < settings.threshold:
        return True

    return active and air < settings.threshold + settings.hysteresis


def apply_second_floor_guard(target: ThermostatTarget, setpoints: FloorSetpoints) -> ThermostatTarget:
    if not target.power or (target.setpoint is not None and target.setpoint >= setpoints.comfort):
        return target

    return ThermostatTarget(power=True, setpoint=setpoints.comfort)
