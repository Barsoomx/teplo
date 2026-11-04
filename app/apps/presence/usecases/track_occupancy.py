from dataclasses import dataclass
from datetime import datetime

import structlog

from apps.core.domain.errors import DomainError
from apps.core.domain.events import EventDispatcher, event_dispatcher
from apps.core.domain.usecases.base import BaseUseCase
from apps.devices.domain.capabilities import PresenceSensorProvider
from apps.devices.services.driver_registry import driver_registry
from apps.presence.domain.events import RoomOccupancyChanged
from apps.presence.domain.occupancy import UNAVAILABLE, Occupancy, PresenceEvidence, decide_occupancy, evidence_of
from apps.presence.domain.ports import OccupancyMemory, OccupancyRoomStore
from apps.presence.domain.rooms import OccupancyRoom
from apps.presence.repositories.occupancy_rooms import OccupancyRoomRepository
from apps.presence.services.occupancy_book import occupancy_book

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class TrackOccupancyInputDTO:
    now: datetime


@dataclass(frozen=True, kw_only=True)
class TrackOccupancyOutputDTO:
    occupancy: dict[str, Occupancy]


class TrackOccupancyUseCase(BaseUseCase[TrackOccupancyInputDTO, TrackOccupancyOutputDTO]):
    def __init__(
        self,
        rooms: OccupancyRoomStore,
        sensors: PresenceSensorProvider,
        book: OccupancyMemory,
        dispatcher: EventDispatcher = event_dispatcher,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self._rooms = rooms
        self._sensors = sensors
        self._book = book

    @classmethod
    def init(cls) -> 'TrackOccupancyUseCase':
        return cls(rooms=OccupancyRoomRepository(), sensors=driver_registry, book=occupancy_book)

    def _execute(self, input_dto: TrackOccupancyInputDTO) -> TrackOccupancyOutputDTO:
        previous = self._book.snapshot()
        occupancy: dict[str, Occupancy] = {}
        for room in self._rooms.occupancy_rooms():
            state = decide_occupancy((self._evidence(slug) for slug in room.sources), room.vacant_after)
            occupancy[room.slug] = state
            self._note(room, state, previous.get(room.slug, Occupancy.UNKNOWN), input_dto.now)
        self._book.replace(occupancy)
        return TrackOccupancyOutputDTO(occupancy=occupancy)

    def _evidence(self, slug: str) -> PresenceEvidence:
        try:
            return evidence_of(self._sensors.presence_sensor(slug).read_presence())
        except DomainError:
            return UNAVAILABLE

    def _note(self, room: OccupancyRoom, state: Occupancy, previous: Occupancy, now: datetime) -> None:
        if state == previous:
            return

        logger.info(
            'room_occupancy_changed',
            room=room.slug,
            occupancy=str(state),
            previous=str(previous),
            live=room.follows_occupancy,
        )
        self._event_store.add_event(
            RoomOccupancyChanged(
                room=room.slug,
                name=room.name,
                occupancy=state,
                previous=previous,
                live=room.follows_occupancy,
                occurred_at=now,
            ),
        )
