from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.kinds import DeviceKind
from apps.devices.repositories.device import DeviceRepository
from apps.devices.services.phase_meter_store import phase_meter_store
from apps.devices.services.presence_store import presence_store
from apps.devices.services.state_store import temperature_state_store


class AvailabilitySnapshot(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def error_code(self) -> str | None: ...

    @property
    def error(self) -> str | None: ...


class AvailabilityStore(Protocol):
    def snapshot(self, slug: str) -> AvailabilitySnapshot: ...

    def last_reported(self, slug: str) -> bool | None: ...

    def mark_reported(self, slug: str, available: bool) -> None: ...


@dataclass(frozen=True, kw_only=True)
class CheckSensorsInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class CheckSensorsOutputDTO:
    available: tuple[str, ...]
    unavailable: tuple[str, ...]


class CheckSensorsUseCase(BaseUseCase[CheckSensorsInputDTO, CheckSensorsOutputDTO]):
    def __init__(
        self,
        devices: DeviceRepository,
        stores: Mapping[DeviceKind, AvailabilityStore],
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._devices = devices
        self._stores = stores

    @classmethod
    def init(cls) -> 'CheckSensorsUseCase':
        return cls(
            devices=DeviceRepository(),
            stores={
                DeviceKind.TEMPERATURE_SENSOR: temperature_state_store,
                DeviceKind.PHASE_METER: phase_meter_store,
                DeviceKind.PRESENCE_SENSOR: presence_store,
            },
        )

    def _execute(self, input_dto: CheckSensorsInputDTO) -> CheckSensorsOutputDTO:
        available: list[str] = []
        unavailable: list[str] = []
        for kind, store in self._stores.items():
            for slug in self._devices.slugs_of_kind(kind):
                (available if self._check(slug, store, input_dto.now) else unavailable).append(slug)
        return CheckSensorsOutputDTO(available=tuple(available), unavailable=tuple(unavailable))

    def _check(self, slug: str, store: AvailabilityStore, now: datetime) -> bool:
        snapshot = store.snapshot(slug)
        if store.last_reported(slug) == snapshot.available:
            return snapshot.available

        store.mark_reported(slug, snapshot.available)
        self._event_store.add_event(
            DeviceAvailabilityChanged(
                slug=slug,
                available=snapshot.available,
                reason='reading received' if snapshot.available else snapshot.error or 'no reading',
                error_code=None if snapshot.available else snapshot.error_code or 'device_unavailable',
                occurred_at=now,
            ),
        )
        return snapshot.available
