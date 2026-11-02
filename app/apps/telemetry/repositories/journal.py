from datetime import datetime

from django.db.models import Max
from django.utils import timezone

from apps.telemetry.models import JournalEntry


class JournalRepository:
    @staticmethod
    def add(kind: str, subject: str, payload: dict[str, object], created_at: datetime) -> None:
        JournalEntry.objects.create(kind=kind, subject=subject, payload=payload, created_at=created_at)

    @staticmethod
    def latest(limit: int) -> list[JournalEntry]:
        return list(JournalEntry.objects.all()[:limit])

    @staticmethod
    def last_activity() -> datetime | None:
        return JournalEntry.objects.aggregate(last=Max('created_at'))['last']

    @staticmethod
    def previous_start_last_activity() -> datetime | None:
        started = JournalEntry.objects.filter(kind='teplo_started').order_by('-pk').first()
        if started is None or not isinstance(started.payload, dict):
            return None

        return _aware_datetime(started.payload.get('last_activity'))


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None

    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None

    return moment if timezone.is_aware(moment) else None
