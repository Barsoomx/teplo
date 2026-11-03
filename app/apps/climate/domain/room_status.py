from dataclasses import dataclass
from decimal import Decimal

from apps.climate.domain.drive import PendingWrite
from apps.climate.domain.room_intent import RoomIntent


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
class RoomStatus:
    slug: str
    name: str
    air: Decimal | None
    intent: RoomIntent
    adjustable: bool
    guest_room: bool
    zones: tuple[ZoneStatus, ...]
