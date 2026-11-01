import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from apps.notifications.domain.alerts import AlertKey, AlertTransition

MESSAGE_LIFETIME = timedelta(hours=1)


class MessageContent(Protocol):
    def text(self, at: datetime) -> str: ...


@dataclass(frozen=True, kw_only=True)
class OutgoingMessage:
    number: int
    content: MessageContent
    queued_at: float
    alert: AlertKey | None = None
    transition: AlertTransition | None = None


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

    def put(
        self,
        content: MessageContent,
        *,
        alert: AlertKey | None = None,
        transition: AlertTransition | None = None,
    ) -> None:
        with self._lock:
            self._last_number += 1
            self._messages.append(
                OutgoingMessage(
                    number=self._last_number,
                    content=content,
                    queued_at=self._clock(),
                    alert=alert,
                    transition=transition,
                ),
            )

    def pending(self) -> tuple[OutgoingMessage, ...]:
        with self._lock:
            return tuple(self._messages)

    def remove(self, message: OutgoingMessage) -> None:
        with self._lock:
            self._messages = [queued for queued in self._messages if queued.number != message.number]

    def drop_expired(self) -> tuple[OutgoingMessage, ...]:
        with self._lock:
            kept_since = self._clock() - self._lifetime.total_seconds()
            expired = tuple(
                message
                for index, message in enumerate(self._messages)
                if _expired(message, self._messages[index + 1 :], kept_since)
            )
            self._messages = [message for message in self._messages if message not in expired]
        return expired


def _expired(message: OutgoingMessage, later: Sequence[OutgoingMessage], kept_since: float) -> bool:
    lifetime_start = _lifetime_start(message, later)
    return lifetime_start is not None and lifetime_start < kept_since


def _lifetime_start(message: OutgoingMessage, later: Sequence[OutgoingMessage]) -> float | None:
    if message.transition is not AlertTransition.RAISED:
        return message.queued_at

    recovery = next(
        (queued for queued in later if queued.alert == message.alert and queued.transition is AlertTransition.CLEARED),
        None,
    )
    return None if recovery is None else recovery.queued_at


alert_outbox = AlertOutbox.init()
