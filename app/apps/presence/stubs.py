from apps.presence.domain.rooms import OccupancyRoom


class OccupancyRoomStoreStub:
    def __init__(self, rooms: list[OccupancyRoom]) -> None:
        self._rooms = rooms

    def occupancy_rooms(self) -> list[OccupancyRoom]:
        return list(self._rooms)
