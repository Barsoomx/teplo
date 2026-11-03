from collections.abc import Mapping, Sequence

from django.db import transaction

from apps.climate.domain.comfort_windows import RoomWeek, StoredWindow
from apps.climate.domain.errors import ComfortWindowNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.models import ComfortPeriod, RoomClimate
from apps.core.repositories.write_lock import lock_for_update
from apps.house.domain.errors import RoomNotFoundError


class ComfortPeriodRepository:
    @staticmethod
    def weeks() -> list[RoomWeek]:
        climates = RoomClimate.objects.select_related('room').prefetch_related('comfort_periods')
        return [_to_week(climate) for climate in climates]

    @staticmethod
    def week_for_update(slug: str) -> RoomWeek:
        if lock_for_update(RoomClimate.objects.filter(room__slug=slug)) == 0:
            raise RoomNotFoundError(slug)

        return _to_week(
            RoomClimate.objects.select_related('room').prefetch_related('comfort_periods').get(room__slug=slug),
        )

    @staticmethod
    def window_week_for_update(window_id: int) -> RoomWeek:
        if lock_for_update(RoomClimate.objects.filter(comfort_periods__id=window_id)) == 0:
            raise ComfortWindowNotFoundError(window_id)

        return _to_week(
            RoomClimate.objects.select_related('room')
            .prefetch_related('comfort_periods')
            .get(comfort_periods__id=window_id),
        )

    @staticmethod
    def replace_days(slug: str, days: Mapping[int, Sequence[ComfortWindow]]) -> None:
        climate = RoomClimate.objects.filter(room__slug=slug).first()
        if climate is None:
            raise RoomNotFoundError(slug)

        with transaction.atomic():
            ComfortPeriod.objects.filter(room_climate=climate, weekday__in=list(days)).delete()
            ComfortPeriod.objects.bulk_create(
                [
                    ComfortPeriod(room_climate=climate, zone=None, weekday=weekday, start=window.start, end=window.end)
                    for weekday in sorted(days)
                    for window in days[weekday]
                ],
            )


def _to_week(climate: RoomClimate) -> RoomWeek:
    return RoomWeek(
        slug=climate.room.slug,
        name=climate.room.name,
        windows=tuple(
            StoredWindow(id=period.pk, weekday=period.weekday, start=period.start, end=period.end)
            for period in climate.comfort_periods.all()
        ),
    )
