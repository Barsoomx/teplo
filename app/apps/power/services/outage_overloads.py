from dataclasses import replace

from apps.power.domain.outage import OutageRecord, overload_window, preceding_overload
from apps.telemetry.repositories.journal import JournalRepository

PHASE_OVERLOAD = 'phase_overload'


class OutageOverloads:
    def __init__(self, journal: JournalRepository) -> None:
        self._journal = journal

    @classmethod
    def init(cls) -> 'OutageOverloads':
        return cls(journal=JournalRepository())

    def noted(self, outage: OutageRecord) -> OutageRecord:
        since, until = overload_window(outage.began_at)
        payload = self._journal.latest_payload(
            PHASE_OVERLOAD,
            since=since,
            until=until,
            exclude={'estimated': True},
        )
        return replace(outage, overload=preceding_overload(payload))
