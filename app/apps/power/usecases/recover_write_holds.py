from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from django.conf import settings

from apps.climate.domain.ports import SourceWrites, WriteGate
from apps.climate.domain.write_guard import MIN_WRITE_INTERVAL
from apps.climate.services.source_write_gate import source_write_gate
from apps.climate.services.write_gate import zone_write_gate
from apps.core.domain.usecases.base import BaseUseCase
from apps.power.domain.loads import LoadRecord
from apps.power.domain.ports import LoadStore
from apps.power.domain.shedding import ShedWriteHold, resumed_write_hold
from apps.power.repositories.load import LoadRepository

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class RecoverWriteHoldsInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class RecoverWriteHoldsOutputDTO:
    zones: tuple[str, ...]
    sources: tuple[str, ...] = ()


class RecoverWriteHoldsUseCase(BaseUseCase[RecoverWriteHoldsInputDTO, RecoverWriteHoldsOutputDTO]):
    def __init__(self, loads: LoadStore, writes: WriteGate, source_writes: SourceWrites, dry_run: bool) -> None:
        super().__init__()
        self._loads = loads
        self._writes = writes
        self._source_writes = source_writes
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'RecoverWriteHoldsUseCase':
        return cls(
            loads=LoadRepository(),
            writes=zone_write_gate,
            source_writes=source_write_gate,
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def _execute(self, input_dto: RecoverWriteHoldsInputDTO) -> RecoverWriteHoldsOutputDTO:
        if self._dry_run:
            return RecoverWriteHoldsOutputDTO(zones=())

        zones: list[ShedWriteHold] = []
        sources: list[ShedWriteHold] = []
        for record in self._loads.all_records():
            hold = resumed_write_hold(record, now=input_dto.now, write_interval=_write_interval(record))
            if hold is None:
                continue

            if record.source_slug is None:
                self._writes.resume(hold.key, hold.at, elapsed=hold.elapsed)
                zones.append(hold)
            else:
                self._source_writes.resume(hold.key, hold.at, elapsed=hold.elapsed)
                sources.append(hold)
        if zones or sources:
            logger.info(
                'shed_write_holds_recovered',
                zones=[hold.key for hold in zones],
                sources=[hold.key for hold in sources],
                elapsed=[str(hold.elapsed) for hold in (*zones, *sources)],
            )
        return RecoverWriteHoldsOutputDTO(
            zones=tuple(hold.key for hold in zones),
            sources=tuple(hold.key for hold in sources),
        )


def _write_interval(record: LoadRecord) -> timedelta:
    return MIN_WRITE_INTERVAL if record.write_interval is None else record.write_interval
