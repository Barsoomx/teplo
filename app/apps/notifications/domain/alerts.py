from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from apps.devices.domain.kinds import DeviceKind


class AlertKind(StrEnum):
    DEVICE_LOST = 'device_lost'
    ZONE_OFF = 'zone_off'
    PHASE_OVERLOAD = 'phase_overload'


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
    last_seen_age: timedelta | None


@dataclass(frozen=True, kw_only=True)
class ZoneHeating:
    slug: str
    name: str
    should_heat: bool
    power: bool | None
    hands_off: bool = False


@dataclass(frozen=True, kw_only=True)
class PhaseAlarm:
    phase: str
    name: str
    alarmed: bool
    amps: Decimal | None
    began_ago: timedelta | None


@dataclass(frozen=True, kw_only=True)
class PhaseReading:
    phase: str
    amps: Decimal | None
    estimated: bool
