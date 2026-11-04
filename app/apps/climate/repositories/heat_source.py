from datetime import datetime

from django.db.models import QuerySet

from apps.climate.domain.heat_sources import FireplaceRecord, HeatSourceKind
from apps.climate.models import HeatSource
from apps.core.repositories.write_lock import lock_for_update


class HeatSourceRepository:
    @staticmethod
    def fireplaces() -> list[FireplaceRecord]:
        return [_to_fireplace(source) for source in _fireplaces().select_related('room')]

    @staticmethod
    def fireplaces_for_update(room: str | None) -> list[FireplaceRecord]:
        rows = _fireplaces() if room is None else _fireplaces().filter(room__slug=room)
        lock_for_update(rows)
        return [_to_fireplace(source) for source in rows.select_related('room')]

    @staticmethod
    def save_lit_until(slug: str, until: datetime | None) -> None:
        HeatSource.objects.filter(slug=slug).update(lit_until=until)


def _fireplaces() -> QuerySet[HeatSource]:
    return HeatSource.objects.filter(kind=HeatSourceKind.FIREPLACE.value)


def _to_fireplace(source: HeatSource) -> FireplaceRecord:
    return FireplaceRecord(
        slug=source.slug,
        name=source.name,
        room_slug=source.room.slug,
        room_name=source.room.name,
        live=source.live,
        lit_until=source.lit_until,
    )
