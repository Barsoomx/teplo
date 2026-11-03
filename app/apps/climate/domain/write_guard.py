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
    zone: str
    slot: WriteSlot
    previous: WriteSlot | None


def write_allowed(
    last_command_at: datetime | None,
    now: datetime,
    min_interval: timedelta = MIN_WRITE_INTERVAL,
) -> bool:
    return last_command_at is None or last_command_at > now or now - last_command_at >= min_interval


def slot_free(
    slot: WriteSlot | None,
    *,
    stamped_at: datetime | None,
    now: datetime,
    read_at: float,
    moment: float,
) -> bool:
    if slot is None:
        return write_allowed(stamped_at, now)

    if not slot.read_by(read_at):
        return _waited(slot, moment)

    return write_allowed(stamped_at, now) and not override_holds(slot, moment)


def override_holds(slot: WriteSlot | None, moment: float) -> bool:
    return slot is not None and slot.override and not _waited(slot, moment)


def _waited(slot: WriteSlot, moment: float) -> bool:
    return moment - slot.moment >= MIN_WRITE_INTERVAL.total_seconds()
