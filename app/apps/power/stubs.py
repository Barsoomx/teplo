from datetime import datetime

from apps.power.domain.outage import OutageCause, OutageRecord


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
