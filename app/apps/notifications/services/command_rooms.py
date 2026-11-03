from apps.house.repositories.house import HouseRepository
from apps.notifications.domain.commands import CommandRoom


class HouseCommandRooms:
    def __init__(self, house: HouseRepository) -> None:
        self._house = house

    @classmethod
    def init(cls) -> 'HouseCommandRooms':
        return cls(house=HouseRepository())

    def rooms(self) -> list[CommandRoom]:
        return [
            CommandRoom(slug=room.slug, name=room.name, level=room.floor_level, stems=room.aliases)
            for room in self._house.rooms()
        ]
