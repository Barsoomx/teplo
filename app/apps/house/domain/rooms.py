from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class RoomRecord:
    slug: str
    name: str
    floor_slug: str
    floor_level: int
    position: int
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class FloorRecord:
    slug: str
    name: str
    level: int
    rooms: tuple[RoomRecord, ...]


def room_aliases(text: str) -> tuple[str, ...]:
    return tuple(text.lower().replace('ё', 'е').split())
