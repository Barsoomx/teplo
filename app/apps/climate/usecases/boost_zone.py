from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.climate.domain.events import ZoneBoosted
from apps.climate.repositories.zone import ZoneRepository
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase

BOOST_DURATION = timedelta(hours=2)


@dataclass(frozen=True, kw_only=True)
class BoostZoneInputDTO:
    zone: str
    now: datetime


@dataclass(frozen=True, kw_only=True)
class BoostZoneOutputDTO:
    zone: str
    until: datetime


class BoostZoneUseCase(BaseUseCase[BoostZoneInputDTO, BoostZoneOutputDTO]):
    def __init__(self, zones: ZoneRepository, dispatcher: EventDispatcher = event_dispatcher) -> None:
        super().__init__(dispatcher=dispatcher)
        self._zones = zones

    @classmethod
    def init(cls) -> 'BoostZoneUseCase':
        return cls(zones=ZoneRepository())

    def _execute(self, input_dto: BoostZoneInputDTO) -> BoostZoneOutputDTO:
        until = input_dto.now + BOOST_DURATION
        self._zones.boost(input_dto.zone, until)
        self._event_store.add_event(ZoneBoosted(zone=input_dto.zone, until=until, occurred_at=input_dto.now))
        return BoostZoneOutputDTO(zone=input_dto.zone, until=until)
