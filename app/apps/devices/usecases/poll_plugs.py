from dataclasses import dataclass
from datetime import datetime

import structlog

from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.capabilities import PlugProvider
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.driver_registry import driver_registry
from apps.devices.services.state_store import PlugStateStore, plug_state_store

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class PollPlugsInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class PollPlugsOutputDTO:
    polled: tuple[str, ...]
    failed: tuple[str, ...]


class PollPlugsUseCase(BaseUseCase[PollPlugsInputDTO, PollPlugsOutputDTO]):
    def __init__(
        self,
        devices: DeviceRepository,
        provider: PlugProvider,
        store: PlugStateStore,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._devices = devices
        self._provider = provider
        self._store = store

    @classmethod
    def init(cls) -> 'PollPlugsUseCase':
        return cls(devices=DeviceRepository(), provider=driver_registry, store=plug_state_store)

    def _execute(self, input_dto: PollPlugsInputDTO) -> PollPlugsOutputDTO:
        polled: list[str] = []
        failed: list[str] = []
        for slug in self._devices.plug_slugs():
            (polled if self._poll(slug) else failed).append(slug)
            self._report_availability(slug, input_dto.now)
        return PollPlugsOutputDTO(polled=tuple(polled), failed=tuple(failed))

    def _poll(self, slug: str) -> bool:
        try:
            state = self._provider.plug(slug).read_state()
        except DomainError as error:
            self._store.record_failure(slug, error_code=error.error_code, error=error.detail)
            logger.warning(
                'plug_poll_failed',
                device=slug,
                error_code=error.error_code,
                detail=error.detail,
            )
            return False

        self._store.record_state(slug, state)
        return True

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
