from collections.abc import Mapping
from typing import Protocol

from apps.presence.domain.occupancy import Occupancy
from apps.presence.domain.rooms import OccupancyRoom


class OccupancyRoomStore(Protocol):
    def occupancy_rooms(self) -> list[OccupancyRoom]: ...


class OccupancyMemory(Protocol):
    def snapshot(self) -> dict[str, Occupancy]: ...

    def replace(self, occupancy: Mapping[str, Occupancy]) -> None: ...
