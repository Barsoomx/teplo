from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

FIREPLACE_MARK = timedelta(hours=4)


class HeatSourceKind(StrEnum):
    HEATER = 'heater'
    AIR_CONDITIONER = 'air_conditioner'
    FIREPLACE = 'fireplace'


@dataclass(frozen=True, kw_only=True)
class FireplaceRecord:
    slug: str
    name: str
    room_slug: str
    room_name: str
    live: bool
    lit_until: datetime | None

    def lit(self, now: datetime) -> bool:
        return self.lit_until is not None and now < self.lit_until <= now + FIREPLACE_MARK


@dataclass(frozen=True, kw_only=True)
class SourceGate:
    id: int
    name: str
    kind: HeatSourceKind
    live: bool
