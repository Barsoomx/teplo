from dataclasses import dataclass, replace
from datetime import datetime

from apps.climate.domain.errors import FireplaceNotFoundError
from apps.climate.domain.events import FireplaceMarkCleared, FireplaceMarked
from apps.climate.domain.heat_sources import FIREPLACE_MARK, FireplaceRecord
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.ports import FireplaceMarkStore
from apps.climate.repositories.heat_source import HeatSourceRepository
from apps.core.domain.events import DomainEvent, EventDispatcher, event_dispatcher
from apps.core.domain.ports import Transaction
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction


@dataclass(frozen=True, kw_only=True)
class MarkFireplaceInputDTO:
    room: str | None
    lit: bool
    now: datetime
    source: CommandSource


@dataclass(frozen=True, kw_only=True)
class MarkFireplaceOutputDTO:
    fireplaces: tuple[FireplaceRecord, ...]
    until: datetime | None
    changed: bool


@dataclass(frozen=True, kw_only=True)
class FireplaceMarking:
    output: MarkFireplaceOutputDTO
    events: tuple[DomainEvent, ...]


class MarkFireplaceUseCase(BaseUseCase[MarkFireplaceInputDTO, MarkFireplaceOutputDTO]):
    def __init__(
        self,
        fireplaces: FireplaceMarkStore,
        transaction: Transaction,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._fireplaces = fireplaces
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'MarkFireplaceUseCase':
        return cls(fireplaces=HeatSourceRepository(), transaction=DatabaseTransaction())

    def _execute(self, input_dto: MarkFireplaceInputDTO) -> MarkFireplaceOutputDTO:
        with self._transaction():
            marking = self._mark(input_dto)
        for event in marking.events:
            self._event_store.add_event(event)
        return marking.output

    def _mark(self, input_dto: MarkFireplaceInputDTO) -> FireplaceMarking:
        fireplaces = self._fireplaces.fireplaces_for_update(input_dto.room)
        if not fireplaces:
            raise FireplaceNotFoundError(input_dto.room)

        if input_dto.lit:
            return self._light(fireplaces, input_dto)

        return self._put_out(fireplaces, input_dto)

    def _light(self, fireplaces: list[FireplaceRecord], input_dto: MarkFireplaceInputDTO) -> FireplaceMarking:
        until = input_dto.now + FIREPLACE_MARK
        for fireplace in fireplaces:
            self._fireplaces.save_lit_until(fireplace.slug, until)
        return FireplaceMarking(
            output=MarkFireplaceOutputDTO(
                fireplaces=tuple(replace(fireplace, lit_until=until) for fireplace in fireplaces),
                until=until,
                changed=True,
            ),
            events=tuple(
                FireplaceMarked(
                    fireplace=fireplace.slug,
                    name=fireplace.name,
                    room=fireplace.room_slug,
                    room_name=fireplace.room_name,
                    until=until,
                    source=input_dto.source,
                    occurred_at=input_dto.now,
                )
                for fireplace in fireplaces
            ),
        )

    def _put_out(self, fireplaces: list[FireplaceRecord], input_dto: MarkFireplaceInputDTO) -> FireplaceMarking:
        for fireplace in fireplaces:
            if fireplace.lit_until is not None:
                self._fireplaces.save_lit_until(fireplace.slug, None)
        lit = [fireplace for fireplace in fireplaces if fireplace.lit(input_dto.now)]
        return FireplaceMarking(
            output=MarkFireplaceOutputDTO(
                fireplaces=tuple(replace(fireplace, lit_until=None) for fireplace in fireplaces),
                until=None,
                changed=bool(lit),
            ),
            events=tuple(
                FireplaceMarkCleared(
                    fireplace=fireplace.slug,
                    name=fireplace.name,
                    room=fireplace.room_slug,
                    room_name=fireplace.room_name,
                    source=input_dto.source,
                    occurred_at=input_dto.now,
                )
                for fireplace in lit
            ),
        )
