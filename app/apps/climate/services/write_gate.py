import itertools
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime

from apps.climate.domain.write_guard import WriteReservation, WriteSlot, override_holds, slot_free


class ZoneWriteGate:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._slots: dict[str, WriteSlot] = {}
        self._generations = itertools.count(1)
        self._lock = threading.Lock()

    @classmethod
    def init(cls) -> 'ZoneWriteGate':
        return cls(clock=time.monotonic)

    def reserve(
        self,
        zone: str,
        at: datetime,
        *,
        stamped_at: datetime | None,
        read_at: float,
    ) -> WriteReservation | None:
        with self._lock:
            moment = self._clock()
            previous = self._slots.get(zone)
            if not slot_free(previous, stamped_at=stamped_at, now=at, read_at=read_at, moment=moment):
                return None

            return self._take(zone, at, moment, override=False)

    def override(self, zone: str, at: datetime) -> WriteReservation:
        with self._lock:
            return self._take(zone, at, self._clock(), override=True)

    @contextmanager
    def stamping(self, reservation: WriteReservation) -> Iterator[None]:
        stored = False
        try:
            yield
            stored = True
        finally:
            if stored:
                self.stored(reservation)
            else:
                self._cancel(reservation)

    def stored(self, reservation: WriteReservation) -> None:
        with self._lock:
            slot = self._slots.get(reservation.zone)
            if slot is not None and slot.generation == reservation.slot.generation:
                self._slots[reservation.zone] = replace(slot, stored_at=self._clock())

    def holds(self, reservation: WriteReservation) -> bool:
        with self._lock:
            slot = self._slots.get(reservation.zone)
            return slot is None or slot.generation == reservation.slot.generation

    def clear(self, zone: str) -> bool:
        with self._lock:
            if override_holds(self._slots.get(zone), self._clock()):
                return False

            self._slots.pop(zone, None)
            return True

    def _take(self, zone: str, at: datetime, moment: float, *, override: bool) -> WriteReservation:
        previous = self._slots.get(zone)
        slot = WriteSlot(generation=next(self._generations), at=at, moment=moment, override=override)
        self._slots[zone] = slot
        return WriteReservation(zone=zone, slot=slot, previous=previous)

    def _cancel(self, reservation: WriteReservation) -> None:
        with self._lock:
            slot = self._slots.get(reservation.zone)
            if slot is None or slot.generation != reservation.slot.generation:
                return

            if reservation.previous is None:
                del self._slots[reservation.zone]
                return

            self._slots[reservation.zone] = reservation.previous


zone_write_gate = ZoneWriteGate.init()
