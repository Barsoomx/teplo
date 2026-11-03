from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from apps.climate.domain.modes import ZoneKind

BOOST_DURATION = timedelta(hours=2)


@dataclass(frozen=True, kw_only=True)
class FloorSetpoints:
    comfort: Decimal
    night: Decimal
    boost: Decimal
    idle: Decimal
    limit: Decimal


@dataclass(frozen=True, kw_only=True)
class AirTargets:
    comfort: Decimal
    night: Decimal


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
    air_sensor_slug: str | None
    heating_enabled: bool
    live_control: bool
    air_control_live: bool
    setpoints: FloorSetpoints
    air_targets: AirTargets
    preheat_lead: timedelta
    windows: tuple[ComfortWindow, ...]
    boost_until: datetime | None
    last_command_at: datetime | None
    last_dry_run_at: datetime | None
    last_air_dry_run_at: datetime | None
    room_slug: str | None = None

    def is_boosted(self, now: datetime) -> bool:
        return self.boost_until is not None and now < self.boost_until <= now + BOOST_DURATION
