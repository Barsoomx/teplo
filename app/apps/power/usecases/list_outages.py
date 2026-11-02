from dataclasses import dataclass

from apps.core.domain.usecases.base import BaseUseCase
from apps.power.domain.outage import OutageRecord
from apps.power.repositories.outage import OutageRepository


@dataclass(frozen=True, kw_only=True)
class ListOutagesInputDTO:
    pass


@dataclass(frozen=True, kw_only=True)
class ListOutagesOutputDTO:
    outages: tuple[OutageRecord, ...]


class ListOutagesUseCase(BaseUseCase[ListOutagesInputDTO, ListOutagesOutputDTO]):
    def __init__(self, outages: OutageRepository) -> None:
        super().__init__()
        self._outages = outages

    @classmethod
    def init(cls) -> 'ListOutagesUseCase':
        return cls(outages=OutageRepository())

    def _execute(self, input_dto: ListOutagesInputDTO) -> ListOutagesOutputDTO:  # noqa: ARG002
        return ListOutagesOutputDTO(outages=tuple(self._outages.newest_first()))
