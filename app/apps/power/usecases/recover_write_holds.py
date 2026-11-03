from dataclasses import dataclass
from datetime import datetime

import structlog
from django.conf import settings

from apps.climate.domain.ports import WriteGate
from apps.climate.domain.write_guard import MIN_WRITE_INTERVAL
from apps.climate.services.write_gate import zone_write_gate
from apps.core.domain.usecases.base import BaseUseCase
from apps.power.domain.ports import LoadStore
from apps.power.domain.shedding import resumed_write_hold
from apps.power.repositories.load import LoadRepository

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class RecoverWriteHoldsInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class RecoverWriteHoldsOutputDTO:
    zones: tuple[str, ...]


class RecoverWriteHoldsUseCase(BaseUseCase[RecoverWriteHoldsInputDTO, RecoverWriteHoldsOutputDTO]):
    def __init__(self, loads: LoadStore, writes: WriteGate, dry_run: bool) -> None:
        super().__init__()
        self._loads = loads
        self._writes = writes
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'RecoverWriteHoldsUseCase':
        return cls(loads=LoadRepository(), writes=zone_write_gate, dry_run=settings.TEPLO_DRY_RUN)

    def _execute(self, input_dto: RecoverWriteHoldsInputDTO) -> RecoverWriteHoldsOutputDTO:
        if self._dry_run:
            return RecoverWriteHoldsOutputDTO(zones=())

        holds = [
            hold
            for hold in (
                resumed_write_hold(record, now=input_dto.now, write_interval=MIN_WRITE_INTERVAL)
                for record in self._loads.all_records()
            )
            if hold is not None
        ]
        for hold in holds:
            self._writes.resume(hold.zone, hold.at, elapsed=hold.elapsed)
        if holds:
            logger.info(
                'shed_write_holds_recovered',
                zones=[hold.zone for hold in holds],
                remaining=[str(MIN_WRITE_INTERVAL - hold.elapsed) for hold in holds],
            )
        return RecoverWriteHoldsOutputDTO(zones=tuple(hold.zone for hold in holds))
