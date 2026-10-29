from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    occurred_at: datetime


EventHandler = Callable[[DomainEvent], None]


class EventStore:
    def __init__(self) -> None:
        self._events: list[DomainEvent] = []

    def add_event(self, event: DomainEvent) -> None:
        self._events.append(event)

    def pop_events(self) -> tuple[DomainEvent, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events


class EventDispatcher:
    def __init__(self) -> None:
        self._handlers: defaultdict[type[DomainEvent], list[EventHandler]] = defaultdict(list)

    def register(self, event_type: type[DomainEvent], handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def dispatch(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            for handler in self._handlers[type(event)]:
                self._call(handler, event)

    @staticmethod
    def _call(handler: EventHandler, event: DomainEvent) -> None:
        try:
            handler(event)
        except Exception:
            logger.exception(
                'domain_event_handler_failed',
                event_type=type(event).__name__,
                handler=getattr(handler, '__qualname__', repr(handler)),
            )


event_dispatcher = EventDispatcher()


def domain_event_handler[TEvent: DomainEvent](
    event_type: type[TEvent],
) -> Callable[[Callable[[TEvent], None]], Callable[[TEvent], None]]:
    def register(handler: Callable[[TEvent], None]) -> Callable[[TEvent], None]:
        event_dispatcher.register(event_type, cast(EventHandler, handler))
        return handler

    return register
