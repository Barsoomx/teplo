from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class OutageRecorded(DomainEvent):
    outage_id: int
    began_at: datetime
    ended_at: datetime
