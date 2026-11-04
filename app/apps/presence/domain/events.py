from dataclasses import dataclass

from apps.core.domain.events import DomainEvent
from apps.presence.domain.occupancy import Occupancy


@dataclass(frozen=True, kw_only=True)
class RoomOccupancyChanged(DomainEvent):
    room: str
    name: str
    occupancy: Occupancy
    previous: Occupancy
    live: bool
