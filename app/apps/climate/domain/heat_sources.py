from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from apps.climate.domain.zone import MANUAL_HOLD

FIREPLACE_MARK = timedelta(hours=4)
SETPOINT_BIAS_DEFAULT = Decimal('0.0')
SETPOINT_BIAS_MIN = Decimal('-5.0')
SETPOINT_BIAS_MAX = Decimal('5.0')
MAX_SETPOINT_DEFAULT = Decimal('24.0')
MAX_SETPOINT_MIN = Decimal('5.0')
MAX_SETPOINT_MAX = Decimal('35.0')
MIN_OUTDOOR_MIN = Decimal('-40.0')
MIN_OUTDOOR_MAX = Decimal('20.0')
MIN_WRITE_MINUTES_DEFAULT = 10
MIN_WRITE_MINUTES_MIN = 1
MIN_WRITE_MINUTES_MAX = 120
RATED_WATTS_MIN = 1
RATED_WATTS_MAX = 10000


class HeatSourceKind(StrEnum):
    HEATER = 'heater'
    AIR_CONDITIONER = 'air_conditioner'
    FIREPLACE = 'fireplace'


COMMANDED_KINDS = (HeatSourceKind.HEATER, HeatSourceKind.AIR_CONDITIONER)


@dataclass(frozen=True, kw_only=True)
class FireplaceRecord:
    slug: str
    name: str
    room_slug: str
    room_name: str
    live: bool
    lit_until: datetime | None
    priority: int = 1

    def lit(self, now: datetime) -> bool:
        return self.lit_until is not None and now < self.lit_until <= now + FIREPLACE_MARK


@dataclass(frozen=True, kw_only=True)
class SourceGate:
    id: int
    name: str
    kind: HeatSourceKind
    live: bool


@dataclass(frozen=True, kw_only=True)
class SourceSettings:
    slug: str
    name: str
    room_slug: str
    room_name: str
    kind: HeatSourceKind
    device_slug: str
    priority: int
    live: bool
    setpoint_bias: Decimal
    max_setpoint: Decimal
    min_outdoor: Decimal | None
    write_interval: timedelta
    rated_watts: int | None
    manual_until: datetime | None
    last_command_at: datetime | None
    last_dry_run_at: datetime | None
    commanded_power: bool | None
    commanded_setpoint: Decimal | None
    load: str | None
    load_live: bool
    load_management_live: bool

    def held_by_hand(self, now: datetime) -> bool:
        return self.manual_until is not None and now < self.manual_until <= now + MANUAL_HOLD

    def writes_live(self, *, dry_run: bool) -> bool:
        return not dry_run and self.live and self.load is not None and self.load_live and self.load_management_live

    def last_write_at(self, *, dry_run: bool) -> datetime | None:
        return self.last_command_at if self.writes_live(dry_run=dry_run) else self.last_dry_run_at
