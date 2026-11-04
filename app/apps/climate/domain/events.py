from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.external_heat import FireEnd
from apps.climate.domain.modes import AirState, CommandSource, ScheduleMode
from apps.core.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class ZoneCommandIssued(DomainEvent):
    zone: str
    change: ThermostatChange
    dry_run: bool


@dataclass(frozen=True, kw_only=True)
class ZoneCommandFailed(DomainEvent):
    zone: str
    change: ThermostatChange
    error: str
    error_code: str


@dataclass(frozen=True, kw_only=True)
class ZoneBoosted(DomainEvent):
    zone: str
    until: datetime


@dataclass(frozen=True, kw_only=True)
class ZoneManualChange(DomainEvent):
    zone: str
    name: str
    power: tuple[bool, bool] | None
    mode: tuple[str, str] | None
    setpoint: tuple[Decimal, Decimal] | None
    until: datetime
    dry_run: bool


@dataclass(frozen=True, kw_only=True)
class AirControlDryRun(DomainEvent):
    zone: str
    air: Decimal
    target: Decimal
    mode: ScheduleMode
    state: AirState
    previous_state: AirState | None
    power: bool
    setpoint: Decimal | None
    start_offset: Decimal
    early_stop_offset: Decimal
    lead_minutes: int | None


@dataclass(frozen=True, kw_only=True)
class OptimalStartDryRun(DomainEvent):
    zone: str
    lead_minutes: int
    start: datetime
    fixed_start: datetime


@dataclass(frozen=True, kw_only=True)
class BackupHeatDryRun(DomainEvent):
    room: str
    room_name: str
    zone: str
    active: bool
    air: Decimal | None
    setpoint: Decimal


@dataclass(frozen=True, kw_only=True)
class GuestsSet(DomainEvent):
    until: datetime
    nights: int
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class GuestsCleared(DomainEvent):
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class RoomOverridden(DomainEvent):
    room: str
    name: str
    target: Decimal
    until: datetime
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class RoomOverrideCleared(DomainEvent):
    room: str
    name: str
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class FireplaceMarked(DomainEvent):
    fireplace: str
    name: str
    room: str
    room_name: str
    until: datetime
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class FireplaceMarkCleared(DomainEvent):
    fireplace: str
    name: str
    room: str
    room_name: str
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class ExternalHeatStarted(DomainEvent):
    room: str
    room_name: str
    rates: dict[str, Decimal]
    live: bool


@dataclass(frozen=True, kw_only=True)
class ExternalHeatEnded(DomainEvent):
    room: str
    room_name: str
    reason: FireEnd
    live: bool


@dataclass(frozen=True, kw_only=True)
class ZoneMorningRejected(DomainEvent):
    zone: str
    name: str
    rate: Decimal
    cap: Decimal
    outdoor_bucket: int
    dead_time_minutes: int


@dataclass(frozen=True, kw_only=True)
class ZoneLearningReset(DomainEvent):
    zone: str
    name: str
    early_stop_offset: Decimal | None
    dead_time_minutes: int | None
    rates: dict[str, str]
    mornings: int
