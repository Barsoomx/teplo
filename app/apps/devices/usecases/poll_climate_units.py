import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import structlog

from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.capabilities import ClimateUnitProvider
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import ClimateUnitState, ReadingPair
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.driver_registry import driver_registry
from apps.devices.services.state_store import ClimateUnitStateStore, climate_unit_state_store, reading_pair

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class PollClimateUnitsInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class PollClimateUnitsOutputDTO:
    polled: tuple[str, ...]
    failed: tuple[str, ...]
    pairs: tuple[ReadingPair[ClimateUnitState], ...]


class PollClimateUnitsUseCase(BaseUseCase[PollClimateUnitsInputDTO, PollClimateUnitsOutputDTO]):
    def __init__(
        self,
        devices: DeviceRepository,
        provider: ClimateUnitProvider,
        store: ClimateUnitStateStore,
        clock: Callable[[], float],
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._devices = devices
        self._provider = provider
        self._store = store
        self._clock = clock

    @classmethod
    def init(cls) -> 'PollClimateUnitsUseCase':
        return cls(
            devices=DeviceRepository(),
            provider=driver_registry,
            store=climate_unit_state_store,
            clock=time.monotonic,
        )

    def _execute(self, input_dto: PollClimateUnitsInputDTO) -> PollClimateUnitsOutputDTO:
        polled: list[str] = []
        failed: list[str] = []
        pairs: list[ReadingPair[ClimateUnitState]] = []
        for slug in self._devices.slugs_of_kind(DeviceKind.CLIMATE_UNIT):
            before = self._store.snapshot(slug)
            seen_at = self._clock()
            state = self._poll(slug)
            if state is None:
                failed.append(slug)
            else:
                polled.append(slug)
                pair = reading_pair(before, state, seen_at=seen_at, after_moment=self._clock())
                if pair is not None:
                    pairs.append(pair)
            self._report_availability(slug, input_dto.now)
        return PollClimateUnitsOutputDTO(polled=tuple(polled), failed=tuple(failed), pairs=tuple(pairs))

    def _poll(self, slug: str) -> ClimateUnitState | None:
        try:
            state = self._provider.climate_unit(slug).read_state()
        except DomainError as error:
            self._store.record_failure(slug, error_code=error.error_code, error=error.detail)
            logger.warning(
                'climate_unit_poll_failed',
                device=slug,
                error_code=error.error_code,
                detail=error.detail,
            )
            return None

        self._store.record_state(slug, state)
        return state

    def _report_availability(self, slug: str, now: datetime) -> None:
        snapshot = self._store.snapshot(slug)
        if self._store.last_reported(slug) == snapshot.available:
            return

        self._store.mark_reported(slug, snapshot.available)
        self._event_store.add_event(
            DeviceAvailabilityChanged(
                slug=slug,
                available=snapshot.available,
                reason='reading received' if snapshot.available else snapshot.error or 'no reading',
                error_code=None if snapshot.available else snapshot.error_code,
                occurred_at=now,
            ),
        )
