from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, kw_only=True)
class OccupancyRoom:
    slug: str
    name: str
    vacant_after: timedelta
    follows_occupancy: bool
    sources: tuple[str, ...]
