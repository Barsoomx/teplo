from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class RoomRecord:
    slug: str
    name: str
    floor_slug: str
    floor_level: int
    position: int


@dataclass(frozen=True, kw_only=True)
class FloorRecord:
    slug: str
    name: str
    level: int
    rooms: tuple[RoomRecord, ...]
