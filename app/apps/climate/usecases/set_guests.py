from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from apps.climate.domain.errors import ClockUntrustedError
from apps.climate.domain.events import GuestsCleared, GuestsSet
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.ports import GuestsStore
from apps.climate.domain.room_intent import MAX_GUEST_NIGHTS, guests_active, guests_end
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.events import DomainEvent, EventDispatcher, event_dispatcher
from apps.core.domain.ports import Transaction
from apps.core.domain.settings import SettingProblem
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction


@dataclass(frozen=True, kw_only=True)
class SetGuestsInputDTO:
    nights: int
    now: datetime
    clock_trusted: bool
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class SetGuestsOutputDTO:
    until: datetime | None


class SetGuestsUseCase(BaseUseCase[SetGuestsInputDTO, SetGuestsOutputDTO]):
    def __init__(
        self,
        guests: GuestsStore,
        transaction: Transaction,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._guests = guests
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'SetGuestsUseCase':
        return cls(guests=ClimateSettingsRepository(), transaction=DatabaseTransaction())

    def _execute(self, input_dto: SetGuestsInputDTO) -> SetGuestsOutputDTO:
        if not 0 <= input_dto.nights <= MAX_GUEST_NIGHTS:
            raise SettingsRejectedError((SettingProblem(field='nights', code='out_of_range'),))

        if input_dto.nights > 0 and not input_dto.clock_trusted:
            raise ClockUntrustedError

        with self._transaction():
            until, event = self._set(input_dto)
        if event is not None:
            self._event_store.add_event(event)
        return SetGuestsOutputDTO(until=until)

    def _set(self, input_dto: SetGuestsInputDTO) -> tuple[datetime | None, DomainEvent | None]:
        guests = self._guests.guests_for_update()
        if input_dto.nights == 0:
            self._guests.save_guests(None)
            if not guests_active(guests.until, input_dto.now):
                return None, None

            return None, GuestsCleared(source=input_dto.source, occurred_at=input_dto.now)

        until = guests_end(timezone.localtime(input_dto.now), input_dto.nights, guests.end_hour)
        self._guests.save_guests(until)
        return until, GuestsSet(
            until=until,
            nights=input_dto.nights,
            source=input_dto.source,
            occurred_at=input_dto.now,
        )
