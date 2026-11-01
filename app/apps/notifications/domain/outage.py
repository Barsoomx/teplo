from dataclasses import dataclass
from datetime import datetime, timedelta

OUTAGE_REPORTED_AFTER = timedelta(minutes=3)


@dataclass(frozen=True, kw_only=True)
class StartupNote:
    last_activity: datetime | None
    monotonic: float


@dataclass(frozen=True, kw_only=True)
class Outage:
    began_at: datetime
    ended_at: datetime


def detect_outage(note: StartupNote, now: datetime, monotonic: float) -> Outage | None:
    started_at = _started_at(note, now, monotonic)
    if note.last_activity is None or started_at - note.last_activity <= OUTAGE_REPORTED_AFTER:
        return None

    return Outage(began_at=note.last_activity, ended_at=started_at)


def started_before_last_activity(note: StartupNote, now: datetime, monotonic: float) -> bool:
    return note.last_activity is not None and _started_at(note, now, monotonic) < note.last_activity


def _started_at(note: StartupNote, now: datetime, monotonic: float) -> datetime:
    return now - timedelta(seconds=monotonic - note.monotonic)
