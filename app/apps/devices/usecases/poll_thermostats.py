import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import structlog

from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.capabilities import ThermostatProvider
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.readings import ReadingPair, ThermostatState
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.driver_registry import driver_registry
from apps.devices.services.state_store import ThermostatSnapshot, ThermostatStateStore, thermostat_state_store

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class PollThermostatsInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class PollThermostatsOutputDTO:
    polled: tuple[str, ...]
    failed: tuple[str, ...]
    pairs: tuple[ReadingPair[ThermostatState], ...]


class PollThermostatsUseCase(BaseUseCase[PollThermostatsInputDTO, PollThermostatsOutputDTO]):
    def __init__(
        self,
        devices: DeviceRepository,
        provider: ThermostatProvider,
        store: ThermostatStateStore,
        clock: Callable[[], float],
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._devices = devices
        self._provider = provider
        self._store = store
        self._clock = clock

    @classmethod
    def init(cls) -> 'PollThermostatsUseCase':
        return cls(
            devices=DeviceRepository(),
            provider=driver_registry,
            store=thermostat_state_store,
            clock=time.monotonic,
        )

    def _execute(self, input_dto: PollThermostatsInputDTO) -> PollThermostatsOutputDTO:
        polled: list[str] = []
        failed: list[str] = []
        pairs: list[ReadingPair[ThermostatState]] = []
        for slug in self._devices.thermostat_slugs():
            before = self._store.snapshot(slug)
            seen_at = self._clock()
            state = self._poll(slug)
            if state is None:
                failed.append(slug)
            else:
                polled.append(slug)
                pair = _pair(before, state, seen_at=seen_at, after_moment=self._clock())
                if pair is not None:
                    pairs.append(pair)
            self._report_availability(slug, input_dto.now)
        return PollThermostatsOutputDTO(polled=tuple(polled), failed=tuple(failed), pairs=tuple(pairs))

    def _poll(self, slug: str) -> ThermostatState | None:
        try:
            state = self._provider.thermostat(slug).read_state()
        except DomainError as error:
            self._store.record_failure(slug, error_code=error.error_code, error=error.detail)
            logger.warning(
                'thermostat_poll_failed',
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


def _pair(
    before: ThermostatSnapshot,
    after: ThermostatState,
    *,
    seen_at: float,
    after_moment: float,
) -> ReadingPair[ThermostatState] | None:
    if before.state is None or before.error_code is not None or before.age is None:
        return None

    return ReadingPair(
        slug=before.slug,
        before=before.state,
        after=after,
        before_moment=seen_at - before.age.total_seconds(),
        after_moment=after_moment,
    )
