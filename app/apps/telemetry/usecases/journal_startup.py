from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.core.domain.usecases.base import BaseUseCase
from apps.telemetry.repositories.journal import JournalRepository
from apps.telemetry.repositories.sample import SampleRepository


@dataclass(frozen=True, kw_only=True)
class JournalStartupInputDTO:
    now: datetime
    boot_id: str | None
    router_uptime: timedelta | None


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
        moments = (
            self._journal.last_activity(),
            self._samples.last_recorded_at(),
            self._journal.previous_start_last_activity(),
        )
        last_activity = max((moment for moment in moments if moment is not None), default=None)
        self._journal.add(
            kind='teplo_started',
            subject='',
            payload={
                'last_activity': last_activity,
                'boot_id': input_dto.boot_id,
                'router_uptime': None if input_dto.router_uptime is None else input_dto.router_uptime.total_seconds(),
            },
            created_at=input_dto.now,
        )
        return JournalStartupOutputDTO(last_activity=last_activity)
