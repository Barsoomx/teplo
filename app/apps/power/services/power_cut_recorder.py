from django.db import transaction

from apps.power.domain.outage import OutageRecord, PowerCut
from apps.power.domain.ports import OutageJournal, OutageStore
from apps.power.repositories.outage import OutageRepository
from apps.telemetry.repositories.journal import JournalRepository

POWER_OUTAGE = 'power_outage'


class PowerCutRecorder:
    def __init__(self, outages: OutageStore, journal: OutageJournal) -> None:
        self._outages = outages
        self._journal = journal

    @classmethod
    def init(cls) -> 'PowerCutRecorder':
        return cls(outages=OutageRepository(), journal=JournalRepository())

    def record(self, power_cut: PowerCut) -> OutageRecord | None:
        with transaction.atomic():
            outage = self._outages.add(power_cut)
            if outage is None:
                return None

            self._journal.add(
                kind=POWER_OUTAGE,
                subject='',
                payload={'outage_id': outage.id, 'began_at': outage.began_at, 'ended_at': outage.ended_at},
                created_at=power_cut.noticed_at,
            )
        return outage
