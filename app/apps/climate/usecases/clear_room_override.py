from dataclasses import dataclass
from datetime import datetime

from apps.climate.domain.events import RoomOverrideCleared
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.ports import RoomOverrideStore
from apps.climate.domain.room_climate import RoomClimateSettings
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.ports import Transaction
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction


@dataclass(frozen=True, kw_only=True)
class ClearRoomOverrideInputDTO:
    room: str
    now: datetime
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class ClearRoomOverrideOutputDTO:
    cleared: bool


class ClearRoomOverrideUseCase(BaseUseCase[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO]):
    def __init__(
        self,
        rooms: RoomOverrideStore,
        transaction: Transaction,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._rooms = rooms
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'ClearRoomOverrideUseCase':
        return cls(rooms=RoomClimateRepository(), transaction=DatabaseTransaction())

    def _execute(self, input_dto: ClearRoomOverrideInputDTO) -> ClearRoomOverrideOutputDTO:
        with self._transaction():
            room = self._clear(input_dto.room)
        if room is None:
            return ClearRoomOverrideOutputDTO(cleared=False)

        self._event_store.add_event(
            RoomOverrideCleared(room=room.slug, name=room.name, source=input_dto.source, occurred_at=input_dto.now),
        )
        return ClearRoomOverrideOutputDTO(cleared=True)

    def _clear(self, slug: str) -> RoomClimateSettings | None:
        room = self._rooms.comfort_for_update(slug)
        if room.override is None:
            return None

        self._rooms.save_override(room.slug, None)
        return room
