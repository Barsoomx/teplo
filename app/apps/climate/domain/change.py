from dataclasses import dataclass
from decimal import Decimal

from apps.climate.domain.floor_policy import ThermostatTarget
from apps.devices.domain.readings import ThermostatMode, ThermostatState


@dataclass(frozen=True, kw_only=True)
class ThermostatChange:
    power: bool | None = None
    mode: ThermostatMode | None = None
    setpoint: Decimal | None = None

    @property
    def is_empty(self) -> bool:
        return self.power is None and self.mode is None and self.setpoint is None


def plan_change(target: ThermostatTarget, state: ThermostatState) -> ThermostatChange:
    if not target.power:
        return ThermostatChange(power=False) if state.power else ThermostatChange()

    return ThermostatChange(
        power=None if state.power else True,
        mode=None if state.mode is ThermostatMode.HOLD else ThermostatMode.HOLD,
        setpoint=None if target.setpoint is None or target.setpoint == state.setpoint else target.setpoint,
    )
