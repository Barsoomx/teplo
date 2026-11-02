from dataclasses import dataclass

import structlog
from django.db import OperationalError

from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.power.domain.events import OutageRecorded
from apps.power.domain.outage import OutageRecord, PowerCut
from apps.power.domain.ports import OutageStore
from apps.power.repositories.outage import OutageRepository
from apps.power.services.outage_backlog import OutageBacklog, outage_backlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class RecordOutagesInputDTO:
    power_cut: PowerCut | None


@dataclass(frozen=True, kw_only=True)
class RecordOutagesOutputDTO:
    recorded: tuple[OutageRecord, ...]


class RecordOutagesUseCase(BaseUseCase[RecordOutagesInputDTO, RecordOutagesOutputDTO]):
    def __init__(
        self,
        outages: OutageStore,
        backlog: OutageBacklog,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._outages = outages
        self._backlog = backlog

    @classmethod
    def init(cls) -> 'RecordOutagesUseCase':
        return cls(outages=OutageRepository(), backlog=outage_backlog)

    def _execute(self, input_dto: RecordOutagesInputDTO) -> RecordOutagesOutputDTO:
        if input_dto.power_cut is not None:
            self._backlog.put(input_dto.power_cut)
        waiting = list(self._backlog.take())
        recorded: list[OutageRecord] = []
        try:
            while waiting:
                outage = self._record(waiting[0])
                if outage is None:
                    break

                recorded.append(outage)
                del waiting[0]
        finally:
            self._backlog.put_back(waiting)
        return RecordOutagesOutputDTO(recorded=tuple(recorded))

    def _record(self, power_cut: PowerCut) -> OutageRecord | None:
        try:
            outage = self._outages.add(began_at=power_cut.began_at, ended_at=power_cut.ended_at)
        except OperationalError as error:
            logger.warning(
                'outage_record_failed',
                began_at=power_cut.began_at.isoformat(),
                ended_at=power_cut.ended_at.isoformat(),
                error=str(error),
            )
            return None

        self._event_store.add_event(
            OutageRecorded(
                outage_id=outage.id,
                began_at=outage.began_at,
                ended_at=outage.ended_at,
                occurred_at=power_cut.noticed_at,
            ),
        )
        return outage
