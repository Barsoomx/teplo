from dataclasses import dataclass
from datetime import datetime

from apps.core.domain.usecases.base import BaseUseCase
from apps.telemetry.repositories.journal import JournalRepository
from apps.telemetry.repositories.sample import SampleRepository


@dataclass(frozen=True, kw_only=True)
class JournalStartupInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class JournalStartupOutputDTO:
    last_activity: datetime | None


class JournalStartupUseCase(BaseUseCase[JournalStartupInputDTO, JournalStartupOutputDTO]):
    def __init__(self, journal: JournalRepository, samples: SampleRepository) -> None:
        super().__init__()
        self._journal = journal
        self._samples = samples

    @classmethod
    def init(cls) -> 'JournalStartupUseCase':
        return cls(journal=JournalRepository(), samples=SampleRepository())

    def _execute(self, input_dto: JournalStartupInputDTO) -> JournalStartupOutputDTO:
        moments = [moment for moment in (self._journal.last_activity(), self._samples.last_recorded_at()) if moment]
        last_activity = max(moments, default=None)
        self._journal.add(
            kind='teplo_started',
            subject='',
            payload={'last_activity': last_activity},
            created_at=input_dto.now,
        )
        return JournalStartupOutputDTO(last_activity=last_activity)
