from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.usecases.base import BaseUseCase
from apps.power.domain.outage import OutageCause, OutageRecord
from apps.power.repositories.outage import OutageRepository


@dataclass(frozen=True, kw_only=True)
class MarkOutageCauseInputDTO:
    outage_id: int
    cause: OutageCause
    now: datetime


@dataclass(frozen=True, kw_only=True)
class MarkOutageCauseOutputDTO:
    outage: OutageRecord


class MarkOutageCauseUseCase(BaseUseCase[MarkOutageCauseInputDTO, MarkOutageCauseOutputDTO]):
    def __init__(self, outages: OutageRepository) -> None:
        super().__init__()
        self._outages = outages

    @classmethod
    def init(cls) -> 'MarkOutageCauseUseCase':
        return cls(outages=OutageRepository())

    def _execute(self, input_dto: MarkOutageCauseInputDTO) -> MarkOutageCauseOutputDTO:
        return MarkOutageCauseOutputDTO(
            outage=self._outages.mark(input_dto.outage_id, input_dto.cause, input_dto.now),
        )
