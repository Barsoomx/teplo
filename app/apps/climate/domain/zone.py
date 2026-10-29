from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from apps.climate.domain.modes import ZoneKind


@dataclass(frozen=True, kw_only=True)
class FloorSetpoints:
    comfort: Decimal
    night: Decimal
    boost: Decimal


@dataclass(frozen=True, kw_only=True)
class ComfortWindow:
    weekday: int
    start: time
    end: time


@dataclass(frozen=True, kw_only=True)
class ZoneSettings:
    slug: str
    name: str
    kind: ZoneKind
    thermostat_slug: str
    heating_enabled: bool
    live_control: bool
    setpoints: FloorSetpoints
    preheat_lead: timedelta
    windows: tuple[ComfortWindow, ...]
    boost_until: datetime | None
    last_command_at: datetime | None

    def is_boosted(self, now: datetime) -> bool:
        return self.boost_until is not None and now < self.boost_until
