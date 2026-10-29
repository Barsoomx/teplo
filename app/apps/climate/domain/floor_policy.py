from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.zone import ZoneSettings


@dataclass(frozen=True, kw_only=True)
class ThermostatTarget:
    power: bool
    setpoint: Decimal | None


def decide_floor_target(
    zone: ZoneSettings,
    mode: ScheduleMode,
    now: datetime,
    *,
    clock_trusted: bool,
) -> ThermostatTarget:
    if not zone.heating_enabled:
        return ThermostatTarget(power=False, setpoint=None)

    if zone.is_boosted(now):
        return ThermostatTarget(power=True, setpoint=zone.setpoints.boost)

    if not clock_trusted or mode is not ScheduleMode.NIGHT:
        return ThermostatTarget(power=True, setpoint=zone.setpoints.comfort)

    return ThermostatTarget(power=True, setpoint=zone.setpoints.night)
