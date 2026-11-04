from dataclasses import dataclass
from datetime import datetime

from apps.climate.domain.events import ZoneBoosted
from apps.climate.domain.ports import WriteGate, ZoneBoostStore
from apps.climate.domain.zone import BOOST_DURATION
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.services.write_gate import zone_write_gate
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase


@dataclass(frozen=True, kw_only=True)
class BoostZoneInputDTO:
    zone: str
    now: datetime


@dataclass(frozen=True, kw_only=True)
class BoostZoneOutputDTO:
    zone: str
    until: datetime


class BoostZoneUseCase(BaseUseCase[BoostZoneInputDTO, BoostZoneOutputDTO]):
    def __init__(
        self,
        zones: ZoneBoostStore,
        writes: WriteGate,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._zones = zones
        self._writes = writes

    @classmethod
    def init(cls) -> 'BoostZoneUseCase':
        return cls(zones=ZoneRepository(), writes=zone_write_gate)

    def _execute(self, input_dto: BoostZoneInputDTO) -> BoostZoneOutputDTO:
        until = input_dto.now + BOOST_DURATION
        with self._writes.clearing((input_dto.zone,)) as freed:
            self._zones.boost(input_dto.zone, until, clear_stamps=input_dto.zone in freed)
        self._event_store.add_event(ZoneBoosted(zone=input_dto.zone, until=until, occurred_at=input_dto.now))
        return BoostZoneOutputDTO(zone=input_dto.zone, until=until)
