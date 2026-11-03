from dataclasses import dataclass
from datetime import datetime, timedelta

MIN_WRITE_INTERVAL = timedelta(minutes=30)


@dataclass(frozen=True, kw_only=True)
class WriteSlot:
    at: datetime
    moment: float


@dataclass(frozen=True, kw_only=True)
class WriteReservation:
    zone: str
    slot: WriteSlot
    previous: WriteSlot | None


def write_allowed(
    last_command_at: datetime | None,
    now: datetime,
    min_interval: timedelta = MIN_WRITE_INTERVAL,
) -> bool:
    return last_command_at is None or last_command_at > now or now - last_command_at >= min_interval


def slot_free(slot: WriteSlot | None, *, stamped_at: datetime | None, now: datetime, moment: float) -> bool:
    if slot is None or (slot.at == stamped_at and slot.at <= now):
        return write_allowed(stamped_at, now)

    return moment - slot.moment >= MIN_WRITE_INTERVAL.total_seconds()
