from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.utils import timezone

from apps.climate.domain.errors import RoomNotAdjustableError
from apps.climate.domain.events import RoomOverridden
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.ports import GuestsStore, RoomOverrideStore
from apps.climate.domain.room_climate import RoomOverride
from apps.climate.domain.room_intent import (
    OVERRIDE_DURATION,
    OverrideChange,
    override_problems,
    override_target,
    resolve_room_intent,
)
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.repositories.room_climate import RoomClimateRepository
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.ports import Transaction
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction


@dataclass(frozen=True, kw_only=True)
class OverrideRoomInputDTO:
    room: str
    change: OverrideChange
    value: Decimal | None
    now: datetime
    clock_trusted: bool
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class OverrideRoomOutputDTO:
    room: str
    name: str
    target: Decimal
    until: datetime


class OverrideRoomUseCase(BaseUseCase[OverrideRoomInputDTO, OverrideRoomOutputDTO]):
    def __init__(
        self,
        rooms: RoomOverrideStore,
        guests: GuestsStore,
        transaction: Transaction,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._rooms = rooms
        self._guests = guests
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'OverrideRoomUseCase':
        return cls(rooms=RoomClimateRepository(), guests=ClimateSettingsRepository(), transaction=DatabaseTransaction())

    def _execute(self, input_dto: OverrideRoomInputDTO) -> OverrideRoomOutputDTO:
        with self._transaction():
            output = self._override(input_dto)
        self._event_store.add_event(
            RoomOverridden(
                room=output.room,
                name=output.name,
                target=output.target,
                until=output.until,
                source=input_dto.source,
                occurred_at=input_dto.now,
            ),
        )
        return output

    def _override(self, input_dto: OverrideRoomInputDTO) -> OverrideRoomOutputDTO:
        room = self._rooms.comfort_for_update(input_dto.room)
        if not room.adjustable:
            raise RoomNotAdjustableError(room.slug)

        intent = resolve_room_intent(
            room,
            now=timezone.localtime(input_dto.now),
            clock_trusted=input_dto.clock_trusted,
            guests_until=self._guests.guests().until,
        )
        target = override_target(intent, room.targets, input_dto.change, input_dto.value)
        problems = override_problems(target)
        if problems:
            raise SettingsRejectedError(problems)

        until = input_dto.now + OVERRIDE_DURATION
        self._rooms.save_override(room.slug, RoomOverride(target=target, until=until, source=input_dto.source))
        return OverrideRoomOutputDTO(room=room.slug, name=room.name, target=target, until=until)
