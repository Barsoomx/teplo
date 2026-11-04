from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.climate.domain.events import ZoneLearningReset
from apps.climate.domain.learning import ZoneLearningEntry
from apps.climate.domain.ports import ZoneLearningResetStore
from apps.climate.repositories.zone_learning import ZoneLearningRepository
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.ports import Transaction
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction

MINUTE = timedelta(minutes=1)


@dataclass(frozen=True, kw_only=True)
class ResetZoneLearningInputDTO:
    zone: str
    now: datetime


@dataclass(frozen=True, kw_only=True)
class ResetZoneLearningOutputDTO:
    reset: bool


class ResetZoneLearningUseCase(BaseUseCase[ResetZoneLearningInputDTO, ResetZoneLearningOutputDTO]):
    def __init__(
        self,
        learning: ZoneLearningResetStore,
        transaction: Transaction,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._learning = learning
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'ResetZoneLearningUseCase':
        return cls(learning=ZoneLearningRepository(), transaction=DatabaseTransaction())

    def _execute(self, input_dto: ResetZoneLearningInputDTO) -> ResetZoneLearningOutputDTO:
        with self._transaction():
            entry = self._reset(input_dto.zone)
        if entry is None:
            return ResetZoneLearningOutputDTO(reset=False)

        learned = entry.record
        self._event_store.add_event(
            ZoneLearningReset(
                zone=entry.zone,
                name=entry.name,
                early_stop_offset=learned.early_stop_offset,
                dead_time_minutes=None if learned.heat_up.dead_time is None else learned.heat_up.dead_time // MINUTE,
                rates={str(bucket): str(rate) for bucket, rate in sorted(learned.heat_up.rates.items())},
                mornings=learned.heat_up.mornings,
                occurred_at=input_dto.now,
            ),
        )
        return ResetZoneLearningOutputDTO(reset=True)

    def _reset(self, zone: str) -> ZoneLearningEntry | None:
        entry = self._learning.record_for_update(zone)
        if entry is None or not entry.record.learned:
            return None

        self._learning.reset(zone)
        return entry
