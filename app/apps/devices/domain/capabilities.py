from decimal import Decimal
from typing import Protocol

from apps.devices.domain.readings import (
    ClimateMode,
    ClimateUnitState,
    EvChargerState,
    PhaseCurrents,
    PlugState,
    TemperatureReading,
    ThermostatMode,
    ThermostatState,
)


class FloorThermostat(Protocol):
    def read_state(self) -> ThermostatState: ...

    def set_power(self, power: bool) -> None: ...

    def set_mode(self, mode: ThermostatMode) -> None: ...

    def set_setpoint(self, setpoint: Decimal) -> None: ...


class MeteredPlug(Protocol):
    def read_state(self) -> PlugState: ...


class SwitchablePlug(MeteredPlug, Protocol):
    def set_on(self, on: bool) -> None: ...


class TemperatureSensor(Protocol):
    def read_temperature(self) -> TemperatureReading: ...


class PhaseMeter(Protocol):
    def read_currents(self) -> PhaseCurrents: ...


class ClimateUnit(Protocol):
    def read_state(self) -> ClimateUnitState: ...

    def set_target(self, mode: ClimateMode, target: Decimal) -> None: ...


class EvCharger(Protocol):
    def read_state(self) -> EvChargerState: ...

    def set_current_limit(self, amps: Decimal) -> None: ...


class ThermostatProvider(Protocol):
    def thermostat(self, slug: str) -> FloorThermostat: ...


class PlugProvider(Protocol):
    def plug(self, slug: str) -> MeteredPlug: ...
