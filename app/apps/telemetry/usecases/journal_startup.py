from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.core.domain.usecases.base import BaseUseCase
from apps.telemetry.domain.starts import BootOpening, TeploStart, boot_opening
from apps.telemetry.repositories.journal import TEPLO_STARTED, JournalRepository
from apps.telemetry.repositories.sample import SampleRepository


@dataclass(frozen=True, kw_only=True)
class JournalStartupInputDTO:
    now: datetime
    boot_id: str | None
    router_uptime: timedelta | None


@dataclass(frozen=True, kw_only=True)
class JournalStartupOutputDTO:
    last_activity: datetime | None
    boot: BootOpening


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
        start = TeploStart(
            boot_id=input_dto.boot_id,
            last_activity=max((moment for moment in moments if moment is not None), default=None),
            router_uptime=input_dto.router_uptime,
        )
        boot = boot_opening(start, self._journal.starts_newest_first())
        self._journal.add(
            kind=TEPLO_STARTED,
            subject='',
            payload={
                'last_activity': start.last_activity,
                'boot_id': start.boot_id,
                'router_uptime': None if start.router_uptime is None else start.router_uptime.total_seconds(),
            },
            created_at=input_dto.now,
        )
        return JournalStartupOutputDTO(last_activity=start.last_activity, boot=boot)
