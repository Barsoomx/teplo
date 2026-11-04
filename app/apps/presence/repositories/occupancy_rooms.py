from datetime import timedelta

from django.db.models import Exists, OuterRef

from apps.climate.domain.room_climate import VACANT_AFTER_MINUTES_DEFAULT
from apps.climate.models import RoomClimate, Zone
from apps.devices.domain.kinds import DeviceKind
from apps.devices.models import Device
from apps.house.models import Room
from apps.presence.domain.rooms import OccupancyRoom


class OccupancyRoomRepository:
    @staticmethod
    def occupancy_rooms() -> list[OccupancyRoom]:
        sources: dict[int, list[str]] = {}
        presence = Device.objects.filter(kind=DeviceKind.PRESENCE_SENSOR).order_by('slug')
        for room_id, slug in presence.values_list('room_id', 'slug'):
            if room_id is not None:
                sources.setdefault(room_id, []).append(slug)
        rooms = (
            Room.objects.filter(pk__in=sources)
            .select_related('climate')
            .annotate(has_floor=Exists(Zone.objects.filter(room=OuterRef('pk'))))
            .order_by('floor__level', 'position', 'name')
        )
        return [_to_room(room, _climate(room), tuple(sources[room.pk]), has_floor=room.has_floor) for room in rooms]


def _climate(room: Room) -> RoomClimate | None:
    try:
        return room.climate
    except RoomClimate.DoesNotExist:
        return None


def _to_room(
    room: Room,
    climate: RoomClimate | None,
    sources: tuple[str, ...],
    *,
    has_floor: bool,
) -> OccupancyRoom:
    minutes = VACANT_AFTER_MINUTES_DEFAULT if climate is None else climate.vacant_after_minutes
    return OccupancyRoom(
        slug=room.slug,
        name=room.name,
        vacant_after=timedelta(minutes=minutes),
        follows_occupancy=climate is not None and climate.follows_occupancy and not has_floor,
        sources=sources,
    )
