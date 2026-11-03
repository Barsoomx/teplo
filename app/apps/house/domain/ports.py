from typing import Protocol

from apps.house.domain.rooms import FloorRecord


class HouseStore(Protocol):
    def floors(self) -> list[FloorRecord]: ...
