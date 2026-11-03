from collections.abc import Mapping, Sequence

from django.db import transaction

from apps.climate.domain.comfort_windows import StoredWindow, ZoneWeek
from apps.climate.domain.errors import ComfortWindowNotFoundError, ZoneNotFoundError
from apps.climate.domain.zone import ComfortWindow
from apps.climate.models import ComfortPeriod, Zone


class ComfortPeriodRepository:
    @staticmethod
    def weeks() -> list[ZoneWeek]:
        return [_to_week(zone) for zone in Zone.objects.prefetch_related('comfort_periods').order_by('slug')]

    @staticmethod
    def week(slug: str) -> ZoneWeek:
        zone = Zone.objects.prefetch_related('comfort_periods').filter(slug=slug).first()
        if zone is None:
            raise ZoneNotFoundError(slug)

        return _to_week(zone)

    @staticmethod
    def window_week(window_id: int) -> ZoneWeek:
        period = ComfortPeriod.objects.select_related('zone').filter(pk=window_id).first()
        if period is None:
            raise ComfortWindowNotFoundError(window_id)

        return ComfortPeriodRepository.week(period.zone.slug)

    @staticmethod
    def replace_days(slug: str, days: Mapping[int, Sequence[ComfortWindow]]) -> None:
        zone = Zone.objects.filter(slug=slug).first()
        if zone is None:
            raise ZoneNotFoundError(slug)

        with transaction.atomic():
            ComfortPeriod.objects.filter(zone=zone, weekday__in=list(days)).delete()
            ComfortPeriod.objects.bulk_create(
                [
                    ComfortPeriod(zone=zone, weekday=weekday, start=window.start, end=window.end)
                    for weekday in sorted(days)
                    for window in days[weekday]
                ],
            )


def _to_week(zone: Zone) -> ZoneWeek:
    return ZoneWeek(
        slug=zone.slug,
        name=zone.name,
        windows=tuple(
            StoredWindow(id=period.pk, weekday=period.weekday, start=period.start, end=period.end)
            for period in zone.comfort_periods.all()
        ),
    )
