from datetime import datetime

from django.db.models import Max

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
