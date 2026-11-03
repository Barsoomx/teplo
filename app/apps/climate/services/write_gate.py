import threading
import time
from collections.abc import Callable
from datetime import datetime

from django.utils import timezone

from apps.climate.domain.write_guard import WriteReservation, WriteSlot, slot_free


class ZoneWriteGate:
    def __init__(self, clock: Callable[[], float], wall_clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._slots: dict[str, WriteSlot] = {}
        self._lock = threading.Lock()

    @classmethod
    def init(cls) -> 'ZoneWriteGate':
        return cls(clock=time.monotonic, wall_clock=timezone.now)

    def reserve(self, zone: str, at: datetime, *, stamped_at: datetime | None) -> WriteReservation | None:
        with self._lock:
            moment = self._clock()
            previous = self._slots.get(zone)
            current = self._wall_clock()
            if not slot_free(previous, stamped_at=stamped_at, now=at, current=current, moment=moment):
                return None

            slot = WriteSlot(at=at, moment=moment)
            self._slots[zone] = slot
            return WriteReservation(zone=zone, slot=slot, previous=previous)

    def override(self, zone: str, at: datetime) -> None:
        with self._lock:
            self._slots[zone] = WriteSlot(at=at, moment=self._clock())

    def holds(self, reservation: WriteReservation) -> bool:
        with self._lock:
            slot = self._slots.get(reservation.zone)
            return slot is None or slot is reservation.slot

    def cancel(self, reservation: WriteReservation) -> None:
        with self._lock:
            if self._slots.get(reservation.zone) is not reservation.slot:
                return

            if reservation.previous is None:
                del self._slots[reservation.zone]
                return

            self._slots[reservation.zone] = reservation.previous

    def clear(self, zone: str) -> None:
        with self._lock:
            self._slots.pop(zone, None)


zone_write_gate = ZoneWriteGate.init()
