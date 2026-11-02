from datetime import datetime
from typing import Protocol

from apps.power.domain.outage import OutageRecord


class OutageStore(Protocol):
    def add(self, began_at: datetime, ended_at: datetime) -> OutageRecord: ...


class OutageJournal(Protocol):
    def add(self, kind: str, subject: str, payload: dict[str, object], created_at: datetime) -> None: ...
