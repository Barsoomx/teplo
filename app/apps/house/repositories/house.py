from apps.house.domain.rooms import FloorRecord, RoomRecord, room_aliases
from apps.house.models import Floor, Room


class HouseRepository:
    @staticmethod
    def floors() -> list[FloorRecord]:
        return [
            FloorRecord(
                slug=floor.slug,
                name=floor.name,
                level=floor.level,
                rooms=tuple(_to_record(room, floor) for room in floor.rooms.all()),
            )
            for floor in Floor.objects.prefetch_related('rooms').order_by('level')
        ]

    @staticmethod
    def rooms() -> list[RoomRecord]:
        return [
            _to_record(room, room.floor)
            for room in Room.objects.select_related('floor').order_by('floor__level', 'position', 'name')
        ]


def _to_record(room: Room, floor: Floor) -> RoomRecord:
    return RoomRecord(
        slug=room.slug,
        name=room.name,
        floor_slug=floor.slug,
        floor_level=floor.level,
        position=room.position,
        aliases=room_aliases(room.aliases),
    )
