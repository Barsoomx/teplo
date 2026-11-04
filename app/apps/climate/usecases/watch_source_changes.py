import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import structlog
from django.conf import settings
from django.db import OperationalError

from apps.climate.domain.errors import HeatSourceNotFoundError
from apps.climate.domain.events import SourceManualChange
from apps.climate.domain.heat_sources import SourceSettings
from apps.climate.domain.manual_change import ManualChange, source_change, teplo_window
from apps.climate.domain.ports import HeatSourceStore, SourceHoldStore, SourceWrites
from apps.climate.domain.zone import MANUAL_HOLD
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.climate.services.source_write_gate import source_write_gate
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.readings import ClimateUnitState, ReadingPair

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class WatchSourceChangesInputDTO:
    pairs: tuple[ReadingPair[ClimateUnitState], ...]
    now: datetime


@dataclass(frozen=True, kw_only=True)
class WatchSourceChangesOutputDTO:
    held: tuple[str, ...]
    journaled: tuple[str, ...]


class WatchSourceChangesUseCase(BaseUseCase[WatchSourceChangesInputDTO, WatchSourceChangesOutputDTO]):
    def __init__(
        self,
        sources: HeatSourceStore,
        holds: SourceHoldStore,
        writes: SourceWrites,
        clock: Callable[[], float],
        dry_run: bool,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._sources = sources
        self._holds = holds
        self._writes = writes
        self._clock = clock
        self._dry_run = dry_run

    @classmethod
    def init(cls) -> 'WatchSourceChangesUseCase':
        return cls(
            sources=HeatSourceRepository(),
            holds=HeatSourceRepository(),
            writes=source_write_gate,
            clock=time.monotonic,
            dry_run=settings.TEPLO_DRY_RUN,
        )

    def _execute(self, input_dto: WatchSourceChangesInputDTO) -> WatchSourceChangesOutputDTO:
        changes = [(pair, change) for pair in input_dto.pairs if (change := source_change(pair)) is not None]
        if not changes:
            return WatchSourceChangesOutputDTO(held=(), journaled=())

        sources = {source.device_slug: source for source in self._sources.commanded_settings()}
        checked_at = self._clock()
        held: list[str] = []
        journaled: list[str] = []
        for pair, change in changes:
            source = sources.get(pair.slug)
            if source is None or self._writes.written_between(source.slug, *teplo_window(pair, checked_at)):
                continue

            live = source.writes_live(dry_run=self._dry_run)
            if self._note(source, change, live=live, now=input_dto.now):
                journaled.append(source.slug)
                if live:
                    held.append(source.slug)
        return WatchSourceChangesOutputDTO(held=tuple(held), journaled=tuple(journaled))

    def _note(self, source: SourceSettings, change: ManualChange, *, live: bool, now: datetime) -> bool:
        until = now + MANUAL_HOLD
        if live and not self._held(source, until):
            return False

        logger.info(
            'source_manual_change',
            source=source.slug,
            power=None if change.power is None else list(change.power),
            setpoint=None if change.setpoint is None else [str(setpoint) for setpoint in change.setpoint],
            until=until.isoformat(),
            live=live,
        )
        self._event_store.add_event(
            SourceManualChange(
                source=source.slug,
                name=source.name,
                power=change.power,
                setpoint=change.setpoint,
                until=until,
                dry_run=not live,
                occurred_at=now,
            ),
        )
        return True

    def _held(self, source: SourceSettings, until: datetime) -> bool:
        try:
            held = self._holds.hold_by_hand(source.slug, until, stamped_at=source.last_command_at)
        except (OperationalError, HeatSourceNotFoundError) as error:
            logger.warning(
                'source_manual_hold_failed',
                source=source.slug,
                error=str(error),
            )
            return False

        if not held:
            logger.info('source_manual_hold_overtaken', source=source.slug)
        return held
