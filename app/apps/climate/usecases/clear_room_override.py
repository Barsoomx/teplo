from dataclasses import dataclass
from datetime import datetime

from apps.climate.domain.events import RoomOverrideCleared
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.ports import GuestsStore, OccupancyStore, RoomOverrideStore
from apps.climate.domain.room_climate import RoomClimateSettings
from apps.climate.domain.room_intent import occupancy_holds
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.ports import Transaction
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction
from apps.presence.domain.occupancy import Occupancy
from apps.presence.services.occupancy_book import occupancy_book


@dataclass(frozen=True, kw_only=True)
class ClearRoomOverrideInputDTO:
    room: str
    now: datetime
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class ClearRoomOverrideOutputDTO:
    cleared: bool
    occupancy: Occupancy | None = None


class ClearRoomOverrideUseCase(BaseUseCase[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO]):
    def __init__(
        self,
        rooms: RoomOverrideStore,
        guests: GuestsStore,
        occupancy: OccupancyStore,
        transaction: Transaction,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._rooms = rooms
        self._guests = guests
        self._occupancy = occupancy
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'ClearRoomOverrideUseCase':
        return cls(
            rooms=RoomClimateRepository(),
            guests=ClimateSettingsRepository(),
            occupancy=occupancy_book,
            transaction=DatabaseTransaction(),
        )

    def _execute(self, input_dto: ClearRoomOverrideInputDTO) -> ClearRoomOverrideOutputDTO:
        with self._transaction():
            room = self._clear(input_dto.room)
            guests_until = None if room is None else self._guests.guests().until
        if room is None:
            return ClearRoomOverrideOutputDTO(cleared=False)

        occupancy = self._occupancy.snapshot().get(room.slug, Occupancy.UNKNOWN)
        holds = occupancy_holds(room, occupancy, guests_until=guests_until, now=input_dto.now)
        returns_to = occupancy if holds else None
        self._event_store.add_event(
            RoomOverrideCleared(
                room=room.slug,
                name=room.name,
                source=input_dto.source,
                occupancy=returns_to,
                occurred_at=input_dto.now,
            ),
        )
        return ClearRoomOverrideOutputDTO(cleared=True, occupancy=returns_to)

    def _clear(self, slug: str) -> RoomClimateSettings | None:
        room = self._rooms.comfort_for_update(slug)
        if room.override is None:
            return None

        self._rooms.save_override(room.slug, None)
        return room
