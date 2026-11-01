import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

MESSAGE_LIFETIME = timedelta(hours=1)


@dataclass(frozen=True, kw_only=True)
class OutgoingMessage:
    number: int
    text: str
    queued_at: float


class AlertOutbox:
    def __init__(self, clock: Callable[[], float], lifetime: timedelta) -> None:
        self._clock = clock
        self._lifetime = lifetime
        self._messages: list[OutgoingMessage] = []
        self._last_number = 0
        self._lock = threading.Lock()

    @classmethod
    def init(cls) -> 'AlertOutbox':
        return cls(clock=time.monotonic, lifetime=MESSAGE_LIFETIME)

    def put(self, text: str) -> None:
        with self._lock:
            self._last_number += 1
            self._messages.append(OutgoingMessage(number=self._last_number, text=text, queued_at=self._clock()))

    def pending(self) -> tuple[OutgoingMessage, ...]:
        with self._lock:
            return tuple(self._messages)

    def remove(self, message: OutgoingMessage) -> None:
        with self._lock:
            self._messages = [queued for queued in self._messages if queued.number != message.number]

    def drop_expired(self) -> tuple[OutgoingMessage, ...]:
        with self._lock:
            kept_since = self._clock() - self._lifetime.total_seconds()
            expired = tuple(message for message in self._messages if message.queued_at < kept_since)
            self._messages = [message for message in self._messages if message.queued_at >= kept_since]
        return expired


alert_outbox = AlertOutbox.init()
