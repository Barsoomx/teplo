from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ThermostatMode(StrEnum):
    HOLD = 'hold'
    SMART = 'smart'
    HOLIDAY = 'holiday'


class ClimateMode(StrEnum):
    OFF = 'off'
    HEAT = 'heat'
    COOL = 'cool'
    AUTO = 'auto'


class Phase(StrEnum):
    A = 'a'
    B = 'b'
    C = 'c'


@dataclass(frozen=True, kw_only=True)
class ThermostatState:
    power: bool
    mode: ThermostatMode
    setpoint: Decimal
    floor_temperature: Decimal
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class PlugState:
    on: bool
    power_watts: Decimal
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class TemperatureReading:
    temperature: Decimal
    humidity: Decimal | None
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class PhaseCurrents:
    amps: dict[Phase, Decimal]
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class ClimateUnitState:
    mode: ClimateMode
    target: Decimal
    room_temperature: Decimal | None
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class EvChargerState:
    charging: bool
    current_limit_amps: Decimal
    read_at: datetime
