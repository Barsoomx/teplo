from collections.abc import Iterator
from datetime import datetime, timedelta

from django.db.models import Max
from django.utils import timezone

from apps.telemetry.domain.starts import TeploStart
from apps.telemetry.models import JournalEntry

TEPLO_STARTED = 'teplo_started'
STARTS_PER_QUERY = 20


class JournalRepository:
    @staticmethod
    def add(kind: str, subject: str, payload: dict[str, object], created_at: datetime) -> None:
        JournalEntry.objects.create(kind=kind, subject=subject, payload=payload, created_at=created_at)

    @staticmethod
    def latest(limit: int) -> list[JournalEntry]:
        return list(JournalEntry.objects.all()[:limit])

    @staticmethod
    def latest_payload(kind: str, *, since: datetime, until: datetime) -> dict[str, object] | None:
        entry = (
            JournalEntry.objects.filter(kind=kind, created_at__gte=since, created_at__lte=until)
            .order_by('-created_at', '-id')
            .first()
        )
        if entry is None:
            return None

        return entry.payload if isinstance(entry.payload, dict) else {}

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
        batch = list(starts[:STARTS_PER_QUERY])
        while batch:
            yield from (_start(started.payload) for started in batch)
            if len(batch) < STARTS_PER_QUERY:
                return

            batch = list(starts.filter(pk__lt=batch[-1].pk)[:STARTS_PER_QUERY])


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
