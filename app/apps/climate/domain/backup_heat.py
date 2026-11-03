from dataclasses import dataclass
from decimal import Decimal

from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.zone import FloorSetpoints


@dataclass(frozen=True, kw_only=True)
class BackupHeat:
    zone_slug: str | None
    below: Decimal
    hysteresis: Decimal
    live: bool


def decide_backup_heat(active: bool, air: Decimal | None, backup: BackupHeat) -> bool:
    if air is None or backup.zone_slug is None:
        return False

    if air < backup.below:
        return True

    return active and air < backup.below + backup.hysteresis


def apply_backup_heat(target: ThermostatTarget, setpoints: FloorSetpoints) -> ThermostatTarget:
    if not target.power or (target.setpoint is not None and target.setpoint >= setpoints.comfort):
        return target

    return ThermostatTarget(power=True, setpoint=setpoints.comfort)
