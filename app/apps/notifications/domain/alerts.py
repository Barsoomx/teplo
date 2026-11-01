from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.devices.domain.kinds import DeviceKind


class AlertKind(StrEnum):
    DEVICE_LOST = 'device_lost'
    ZONE_OFF = 'zone_off'


class AlertTransition(StrEnum):
    RAISED = 'raised'
    CLEARED = 'cleared'


@dataclass(frozen=True, kw_only=True)
class AlertKey:
    kind: AlertKind
    subject: str


@dataclass(frozen=True, kw_only=True)
class MonitoredDevice:
    slug: str
    name: str
    kind: DeviceKind
    available: bool
    last_seen_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class ZoneHeating:
    slug: str
    name: str
    should_heat: bool
    power: bool | None
