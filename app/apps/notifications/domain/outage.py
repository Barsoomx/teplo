from dataclasses import dataclass
from datetime import datetime, timedelta

OUTAGE_REPORTED_AFTER = timedelta(minutes=3)
POWER_CUT_UPTIME = timedelta(minutes=10)


@dataclass(frozen=True, kw_only=True)
class StartupNote:
    last_activity: datetime | None
    monotonic: float
    router_uptime: timedelta | None


@dataclass(frozen=True, kw_only=True)
class Outage:
    began_at: datetime
    ended_at: datetime
    power_cut: bool


def detect_outage(note: StartupNote, now: datetime, monotonic: float) -> Outage | None:
    started_at = _started_at(note, now, monotonic)
    if note.last_activity is None or started_at < note.last_activity:
        return None

    power_cut = _follows_power_cut(note, note.last_activity, started_at)
    if not power_cut and started_at - note.last_activity <= OUTAGE_REPORTED_AFTER:
        return None

    return Outage(began_at=note.last_activity, ended_at=started_at, power_cut=power_cut)


def started_before_last_activity(note: StartupNote, now: datetime, monotonic: float) -> bool:
    return note.last_activity is not None and _started_at(note, now, monotonic) < note.last_activity


def _follows_power_cut(note: StartupNote, last_activity: datetime, started_at: datetime) -> bool:
    if note.router_uptime is None or note.router_uptime >= POWER_CUT_UPTIME:
        return False

    router_came_up_at = started_at - note.router_uptime
    return last_activity < router_came_up_at


def _started_at(note: StartupNote, now: datetime, monotonic: float) -> datetime:
    return now - timedelta(seconds=monotonic - note.monotonic)
