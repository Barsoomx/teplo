from dataclasses import dataclass
from datetime import datetime, timedelta

MIN_WRITE_INTERVAL = timedelta(minutes=30)


@dataclass(frozen=True, kw_only=True)
class WriteSlot:
    generation: int
    at: datetime
    moment: float
    override: bool = False
    stored_at: float | None = None

    def read_by(self, read_at: float) -> bool:
        return self.stored_at is not None and self.stored_at <= read_at


@dataclass(frozen=True, kw_only=True)
class WriteReservation:
    key: str
    slot: WriteSlot
    previous: WriteSlot | None


def write_allowed(
    last_command_at: datetime | None,
    now: datetime,
    min_interval: timedelta = MIN_WRITE_INTERVAL,
) -> bool:
    return last_command_at is None or last_command_at > now or now - last_command_at >= min_interval


def next_write_at(
    last_write_at: datetime | None,
    now: datetime,
    min_interval: timedelta = MIN_WRITE_INTERVAL,
) -> datetime:
    if last_write_at is None or write_allowed(last_write_at, now, min_interval):
        return now

    return last_write_at + min_interval


def slot_free(
    slot: WriteSlot | None,
    *,
    stamped_at: datetime | None,
    now: datetime,
    read_at: float,
    moment: float,
    interval: timedelta = MIN_WRITE_INTERVAL,
) -> bool:
    if slot is None:
        return write_allowed(stamped_at, now, interval)

    if not slot.read_by(read_at):
        return _waited(slot, moment, interval)

    return write_allowed(stamped_at, now, interval) and not override_holds(slot, moment, interval)


def override_holds(slot: WriteSlot | None, moment: float, interval: timedelta = MIN_WRITE_INTERVAL) -> bool:
    return slot is not None and slot.override and not _waited(slot, moment, interval)


def _waited(slot: WriteSlot, moment: float, interval: timedelta) -> bool:
    return moment - slot.moment >= interval.total_seconds()
