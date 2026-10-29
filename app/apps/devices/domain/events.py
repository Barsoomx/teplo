from dataclasses import dataclass

from apps.core.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class DeviceAvailabilityChanged(DomainEvent):
    slug: str
    available: bool
    reason: str
