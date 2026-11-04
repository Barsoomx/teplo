import itertools
import threading
import time
from collections.abc import Callable, Collection, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta

from apps.climate.domain.write_guard import (
    MIN_WRITE_INTERVAL,
    WriteReservation,
    WriteSlot,
    override_holds,
    slot_free,
)

WRITE_HISTORY = timedelta(minutes=5)

KeyLock = AbstractContextManager[object]


class KeyedWriteGate:
    def __init__(self, clock: Callable[[], float], key_lock: Callable[[], KeyLock]) -> None:
        self._clock = clock
        self._key_lock = key_lock
        self._slots: dict[str, WriteSlot] = {}
        self._history: dict[str, tuple[float, ...]] = {}
        self._key_locks: dict[str, KeyLock] = {}
        self._generations = itertools.count(1)
        self._lock = threading.Lock()

    @classmethod
    def init(cls) -> 'KeyedWriteGate':
        return cls(clock=time.monotonic, key_lock=threading.Lock)

    def allows(
        self,
        key: str,
        at: datetime,
        *,
        stamped_at: datetime | None,
        read_at: float,
        interval: timedelta = MIN_WRITE_INTERVAL,
    ) -> bool:
        with self._lock:
            return slot_free(
                self._slots.get(key),
                stamped_at=stamped_at,
                now=at,
                read_at=read_at,
                moment=self._clock(),
                interval=interval,
            )

    def reserve(
        self,
        key: str,
        at: datetime,
        *,
        stamped_at: datetime | None,
        read_at: float,
        interval: timedelta = MIN_WRITE_INTERVAL,
    ) -> WriteReservation | None:
        with self._lock:
            moment = self._clock()
            previous = self._slots.get(key)
            free = slot_free(
                previous,
                stamped_at=stamped_at,
                now=at,
                read_at=read_at,
                moment=moment,
                interval=interval,
            )
            if not free:
                return None

            return self._take(key, at, moment, override=False)

    def override(self, key: str, at: datetime) -> WriteReservation:
        with self._lock:
            return self._take(key, at, self._clock(), override=True)

    def written_between(self, key: str, since: float, until: float) -> bool:
        with self._lock:
            return any(since <= moment <= until for moment in self._recent(key, self._clock()))

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
            slot = self._slots.get(reservation.key)
            if slot is not None and slot.generation == reservation.slot.generation:
                self._slots[reservation.key] = replace(slot, stored_at=self._clock())

    def write(self, reservation: WriteReservation, command: Callable[[], None]) -> bool:
        with self._locked(reservation.key):
            if not self._holds(reservation):
                return False

            command()
            return True

    def send(self, reservation: WriteReservation, command: Callable[[], None]) -> None:
        with self._locked(reservation.key):
            command()

    def resume(self, key: str, at: datetime, *, elapsed: timedelta) -> None:
        with self._lock:
            if key in self._slots:
                return

            moment = self._clock()
            self._slots[key] = WriteSlot(
                generation=next(self._generations),
                at=at,
                moment=moment - elapsed.total_seconds(),
                override=True,
                stored_at=moment,
            )

    @contextmanager
    def clearing(
        self,
        keys: Collection[str],
        *,
        interval: timedelta = MIN_WRITE_INTERVAL,
    ) -> Iterator[frozenset[str]]:
        with self._lock:
            moment = self._clock()
            seen = {key: self._slots.get(key) for key in keys}
        freed = frozenset(key for key, slot in seen.items() if not override_holds(slot, moment, interval))
        yield freed
        with self._lock:
            for key in freed:
                self._free(key, seen[key])

    def _holds(self, reservation: WriteReservation) -> bool:
        with self._lock:
            slot = self._slots.get(reservation.key)
            return slot is None or slot.generation == reservation.slot.generation

    def _locked(self, key: str) -> KeyLock:
        with self._lock:
            if key not in self._key_locks:
                self._key_locks[key] = self._key_lock()
            return self._key_locks[key]

    def _free(self, key: str, seen: WriteSlot | None) -> None:
        slot = self._slots.get(key)
        if slot is not None and seen is not None and slot.generation == seen.generation:
            del self._slots[key]

    def _take(self, key: str, at: datetime, moment: float, *, override: bool) -> WriteReservation:
        previous = self._slots.get(key)
        slot = WriteSlot(generation=next(self._generations), at=at, moment=moment, override=override)
        self._slots[key] = slot
        self._history[key] = (*self._recent(key, moment), moment)
        return WriteReservation(key=key, slot=slot, previous=previous)

    def _recent(self, key: str, moment: float) -> tuple[float, ...]:
        return tuple(taken for taken in self._history.get(key, ()) if moment - taken < WRITE_HISTORY.total_seconds())

    def _cancel(self, reservation: WriteReservation) -> None:
        with self._lock:
            slot = self._slots.get(reservation.key)
            if slot is None or slot.generation != reservation.slot.generation:
                return

            if reservation.previous is None:
                del self._slots[reservation.key]
                return

            self._slots[reservation.key] = reservation.previous


zone_write_gate = KeyedWriteGate.init()
