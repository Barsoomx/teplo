from dataclasses import dataclass
from datetime import datetime

from apps.climate.domain.events import ZoneBoosted
from apps.climate.domain.ports import WriteGate, ZoneBoostStore
from apps.climate.domain.zone import BOOST_DURATION
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.services.write_gate import zone_write_gate
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.ports import Transaction
from apps.core.domain.usecases.base import BaseUseCase
from apps.core.services.database_transaction import DatabaseTransaction


@dataclass(frozen=True, kw_only=True)
class BoostZonesInputDTO:
    zones: tuple[str, ...]
    now: datetime


@dataclass(frozen=True, kw_only=True)
class BoostZonesOutputDTO:
    zones: tuple[str, ...]
    until: datetime


class BoostZonesUseCase(BaseUseCase[BoostZonesInputDTO, BoostZonesOutputDTO]):
    def __init__(
        self,
        zones: ZoneBoostStore,
        writes: WriteGate,
        transaction: Transaction,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._zones = zones
        self._writes = writes
        self._transaction = transaction

    @classmethod
    def init(cls) -> 'BoostZonesUseCase':
        return cls(zones=ZoneRepository(), writes=zone_write_gate, transaction=DatabaseTransaction())

    def _execute(self, input_dto: BoostZonesInputDTO) -> BoostZonesOutputDTO:
        until = input_dto.now + BOOST_DURATION
        with self._writes.clearing(input_dto.zones) as freed, self._transaction():
            for zone in input_dto.zones:
                self._zones.boost(zone, until, clear_stamps=zone in freed)
        for zone in input_dto.zones:
            self._event_store.add_event(ZoneBoosted(zone=zone, until=until, occurred_at=input_dto.now))
        return BoostZonesOutputDTO(zones=input_dto.zones, until=until)
