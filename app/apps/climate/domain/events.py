from dataclasses import dataclass
from datetime import datetime

from apps.climate.domain.change import ThermostatChange
from apps.core.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class ZoneCommandIssued(DomainEvent):
    zone: str
    change: ThermostatChange
    dry_run: bool


@dataclass(frozen=True, kw_only=True)
class ZoneCommandFailed(DomainEvent):
    zone: str
    change: ThermostatChange
    error: str
    error_code: str


@dataclass(frozen=True, kw_only=True)
class ZoneBoosted(DomainEvent):
    zone: str
    until: datetime
