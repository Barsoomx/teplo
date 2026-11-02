from datetime import datetime

from apps.power.domain.outage import OutageCause, OutageRecord

JournalLine = tuple[str, str, dict[str, object], datetime]


class OutageStoreStub:
    def __init__(self, errors: tuple[Exception, ...]) -> None:
        self._errors = errors
        self._attempts = 0
        self._outages: list[OutageRecord] = []

    def add(self, began_at: datetime, ended_at: datetime) -> OutageRecord:
        self._attempts += 1
        if self._attempts <= len(self._errors):
            raise self._errors[self._attempts - 1]

        outage = OutageRecord(
            id=len(self._outages) + 1,
            began_at=began_at,
            ended_at=ended_at,
            cause=OutageCause.UNMARKED,
            cause_marked_at=None,
        )
        self._outages.append(outage)
        return outage


class OutageJournalStub:
    def __init__(self, errors: tuple[Exception, ...]) -> None:
        self._errors = errors
        self._attempts = 0
        self._lines: list[JournalLine] = []

    @property
    def lines(self) -> list[JournalLine]:
        return self._lines

    def add(self, kind: str, subject: str, payload: dict[str, object], created_at: datetime) -> None:
        self._attempts += 1
        if self._attempts <= len(self._errors):
            raise self._errors[self._attempts - 1]

        self._lines.append((kind, subject, payload, created_at))
