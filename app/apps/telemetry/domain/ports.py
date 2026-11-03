from datetime import datetime
from typing import Protocol


class JournalWriter(Protocol):
    def add(self, kind: str, subject: str, payload: dict[str, object], created_at: datetime) -> None: ...
