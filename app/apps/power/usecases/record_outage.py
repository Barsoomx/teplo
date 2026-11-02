from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.power.domain.events import OutageRecorded
from apps.power.domain.outage import OutageRecord
from apps.power.repositories.outage import OutageRepository


@dataclass(frozen=True, kw_only=True)
class RecordOutageInputDTO:
    began_at: datetime
    ended_at: datetime
    now: datetime


@dataclass(frozen=True, kw_only=True)
class RecordOutageOutputDTO:
    outage: OutageRecord


class RecordOutageUseCase(BaseUseCase[RecordOutageInputDTO, RecordOutageOutputDTO]):
    def __init__(self, outages: OutageRepository, dispatcher: EventDispatcher = event_dispatcher) -> None:
        super().__init__(dispatcher=dispatcher)
        self._outages = outages

    @classmethod
    def init(cls) -> 'RecordOutageUseCase':
        return cls(outages=OutageRepository())

    def _execute(self, input_dto: RecordOutageInputDTO) -> RecordOutageOutputDTO:
        outage = self._outages.add(began_at=input_dto.began_at, ended_at=input_dto.ended_at)
        self._event_store.add_event(
            OutageRecorded(
                outage_id=outage.id,
                began_at=outage.began_at,
                ended_at=outage.ended_at,
                occurred_at=input_dto.now,
            ),
        )
        return RecordOutageOutputDTO(outage=outage)
