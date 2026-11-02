from collections.abc import Iterator
from datetime import datetime, timedelta

from django.db.models import Max
from django.utils import timezone

from apps.telemetry.domain.starts import TeploStart
from apps.telemetry.models import JournalEntry

TEPLO_STARTED = 'teplo_started'


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
        started = JournalEntry.objects.filter(kind=TEPLO_STARTED).order_by('-pk').first()
        if started is None or not isinstance(started.payload, dict):
            return None

        return _aware_datetime(started.payload.get('last_activity'))

    @staticmethod
    def starts_newest_first() -> Iterator[TeploStart]:
        starts = JournalEntry.objects.filter(kind=TEPLO_STARTED).order_by('-pk')
        started = starts.first()
        while started is not None:
            yield _start(started.payload)
            started = starts.filter(pk__lt=started.pk).first()


def _start(payload: object) -> TeploStart:
    if not isinstance(payload, dict):
        return TeploStart(boot_id=None, last_activity=None, router_uptime=None)

    boot_id = payload.get('boot_id')
    return TeploStart(
        boot_id=boot_id if isinstance(boot_id, str) and boot_id else None,
        last_activity=_aware_datetime(payload.get('last_activity')),
        router_uptime=_duration(payload.get('router_uptime')),
    )


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None

    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None

    return moment if timezone.is_aware(moment) else None


def _duration(seconds: object) -> timedelta | None:
    if isinstance(seconds, bool) or not isinstance(seconds, int | float) or not seconds >= 0:
        return None

    try:
        return timedelta(seconds=seconds)
    except OverflowError:
        return None
