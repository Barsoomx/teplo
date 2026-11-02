from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.telemetry.domain.starts import BootOpening

OUTAGE_REPORTED_AFTER = timedelta(minutes=3)
POWER_CUT_UPTIME = timedelta(minutes=10)


@dataclass(frozen=True, kw_only=True)
class StartupNote:
    last_activity: datetime | None
    monotonic: float
    router_uptime: timedelta | None
    boot: BootOpening


@dataclass(frozen=True, kw_only=True)
class Outage:
    began_at: datetime
    ended_at: datetime
    power_cut: bool


@dataclass(frozen=True, kw_only=True)
class StartOutages:
    power_cut: Outage | None
    restart: Outage | None
    held_back: bool


def detect_outages(note: StartupNote, now: datetime, monotonic: float) -> StartOutages:
    started_at = now - timedelta(seconds=monotonic - note.monotonic)
    power_cut = _power_cut(note, started_at)
    power_cut_held_back = power_cut is not None and power_cut.ended_at < power_cut.began_at
    held_back = power_cut_held_back or (note.last_activity is not None and started_at < note.last_activity)
    return StartOutages(
        power_cut=None if power_cut_held_back else power_cut,
        restart=None if held_back else _restart(note, started_at, power_cut),
        held_back=held_back,
    )


def _power_cut(note: StartupNote, started_at: datetime) -> Outage | None:
    opening = note.boot.start
    if note.router_uptime is None or opening.last_activity is None or opening.router_uptime is None:
        return None

    came_up_at = started_at - note.router_uptime
    if not _follows_power_cut(note.boot, came_up_at):
        return None

    return Outage(began_at=opening.last_activity, ended_at=came_up_at + opening.router_uptime, power_cut=True)


def _follows_power_cut(boot: BootOpening, came_up_at: datetime) -> bool:
    opening = boot.start
    if opening.boot_id is not None and boot.previous_boot_id is not None:
        return opening.boot_id != boot.previous_boot_id

    return (
        opening.router_uptime is not None
        and opening.router_uptime < POWER_CUT_UPTIME
        and opening.last_activity is not None
        and opening.last_activity < came_up_at
    )


def _restart(note: StartupNote, started_at: datetime, power_cut: Outage | None) -> Outage | None:
    if note.last_activity is None:
        return None

    if power_cut is not None and note.last_activity < power_cut.ended_at:
        return None

    if started_at - note.last_activity <= OUTAGE_REPORTED_AFTER:
        return None

    return Outage(began_at=note.last_activity, ended_at=started_at, power_cut=False)
