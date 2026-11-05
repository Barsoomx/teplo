from dataclasses import dataclass
from datetime import datetime, timedelta
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
    OTHER = 'other'


class ClimateModeWord(StrEnum):
    HEAT_COOL = 'heat_cool'
    DRY = 'dry'
    FAN_ONLY = 'fan_only'


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
    heating: bool | None
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class PlugState:
    on: bool
    power_watts: Decimal
    current_amps: Decimal
    voltage_volts: Decimal
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
    level: str | None = None
    mode_word: ClimateModeWord | None = None

    @property
    def on(self) -> bool:
        return self.mode is not ClimateMode.OFF


@dataclass(frozen=True, kw_only=True)
class PresenceReading:
    present: bool
    seen_ago: timedelta | None
    watched_for: timedelta
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class EvChargerState:
    charging: bool
    current_limit_amps: Decimal
    read_at: datetime


@dataclass(frozen=True, kw_only=True)
class ReadingPair[TState]:
    slug: str
    before: TState
    after: TState
    before_moment: float
    after_moment: float
