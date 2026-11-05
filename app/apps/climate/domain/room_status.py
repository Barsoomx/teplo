from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from apps.climate.domain.drive import PendingWrite
from apps.climate.domain.heat_sources import HeatSourceKind
from apps.climate.domain.room_intent import RoomIntent
from apps.devices.domain.readings import ClimateMode
from apps.presence.domain.occupancy import Occupancy


@dataclass(frozen=True, kw_only=True)
class ZoneStatus:
    slug: str
    name: str
    available: bool
    air: Decimal | None
    floor_temperature: Decimal | None
    setpoint: Decimal | None
    power: bool | None
    air_path: bool
    floor_target: Decimal | None
    pending: PendingWrite | None


@dataclass(frozen=True, kw_only=True)
class SourceStatus:
    slug: str
    name: str
    kind: HeatSourceKind
    room: str
    device: str
    priority: int
    live: bool
    available: bool
    error_code: str | None
    error: str | None
    on: bool | None
    setpoint: Decimal | None
    level: str | None
    temperature: Decimal | None
    target_on: bool | None
    target_setpoint: Decimal | None
    commands_live: bool
    held_until: datetime | None
    shed: bool
    pending: PendingWrite | None
    mode: ClimateMode | None = None
    locked_out: bool = False
    outdoor: Decimal | None = None


@dataclass(frozen=True, kw_only=True)
class RoomStatus:
    slug: str
    name: str
    air: Decimal | None
    intent: RoomIntent
    adjustable: bool
    guest_room: bool
    zones: tuple[ZoneStatus, ...]
    sources: tuple[SourceStatus, ...] = ()
    occupancy: Occupancy | None = None
    follows_occupancy: bool = False
