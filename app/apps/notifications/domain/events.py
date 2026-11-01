from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.events import DomainEvent
from apps.devices.domain.kinds import DeviceKind
from apps.notifications.domain.alerts import AlertKind
from apps.notifications.domain.outage import Outage


@dataclass(frozen=True, kw_only=True)
class AlertEvent(DomainEvent):
    kind: AlertKind
    subject: str
    name: str
    device_kind: DeviceKind
    since: datetime | None


@dataclass(frozen=True, kw_only=True)
class AlertRaised(AlertEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class AlertCleared(AlertEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class TeploResumed(DomainEvent):
    outage: Outage
