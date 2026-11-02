from collections.abc import Collection
from datetime import datetime

from apps.power.domain.errors import OutageNotFoundError
from apps.power.domain.outage import OutageCause, OutageRecord
from apps.power.models import Outage


class OutageRepository:
    @staticmethod
    def add(began_at: datetime, ended_at: datetime) -> OutageRecord:
        return _to_record(Outage.objects.create(began_at=began_at, ended_at=ended_at))

    @staticmethod
    def newest_first() -> list[OutageRecord]:
        return [_to_record(outage) for outage in Outage.objects.order_by('-began_at', '-id')]

    @staticmethod
    def unmarked() -> list[OutageRecord]:
        outages = Outage.objects.filter(cause=OutageCause.UNMARKED).order_by('-began_at', '-id')
        return [_to_record(outage) for outage in outages]

    @staticmethod
    def by_ids(outage_ids: Collection[int]) -> dict[int, OutageRecord]:
        return {outage.pk: _to_record(outage) for outage in Outage.objects.filter(pk__in=outage_ids)}

    @staticmethod
    def mark(outage_id: int, cause: OutageCause, at: datetime) -> OutageRecord:
        updated = Outage.objects.filter(pk=outage_id).update(cause=cause, cause_marked_at=at)
        if updated == 0:
            raise OutageNotFoundError(outage_id)

        return _to_record(Outage.objects.get(pk=outage_id))


def _to_record(outage: Outage) -> OutageRecord:
    return OutageRecord(
        id=outage.pk,
        began_at=outage.began_at,
        ended_at=outage.ended_at,
        cause=OutageCause(outage.cause),
        cause_marked_at=outage.cause_marked_at,
    )
