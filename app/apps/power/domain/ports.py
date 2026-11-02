from datetime import datetime
from typing import Protocol

from apps.power.domain.outage import OutageRecord, PowerCut


class OutageStore(Protocol):
    def add(self, power_cut: PowerCut) -> OutageRecord | None: ...


class OutageJournal(Protocol):
    def add(self, kind: str, subject: str, payload: dict[str, object], created_at: datetime) -> None: ...
