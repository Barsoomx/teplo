from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from apps.core.domain.events import DomainEvent, EventDispatcher, EventStore


@dataclass(frozen=True, kw_only=True)
class ZoneTouched(DomainEvent):
    zone: str


@dataclass(frozen=True, kw_only=True)
class ZoneIgnored(DomainEvent):
    zone: str


def _failing_handler(event: DomainEvent) -> None:
    raise RuntimeError(f'cannot handle {event}')


@pytest.fixture
def f_event() -> ZoneTouched:
    return ZoneTouched(zone='kitchen', occurred_at=datetime(2026, 10, 29, 6, 0, tzinfo=UTC))


@pytest.fixture
def f_store(f_event: ZoneTouched) -> EventStore:
    store = EventStore()
    store.add_event(f_event)
    return store


@pytest.fixture
def f_received() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_ignored() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_dispatcher(f_received: list[DomainEvent], f_ignored: list[DomainEvent]) -> EventDispatcher:
    dispatcher = EventDispatcher()
    dispatcher.register(ZoneTouched, _failing_handler)
    dispatcher.register(ZoneTouched, f_received.append)
    dispatcher.register(ZoneIgnored, f_ignored.append)
    return dispatcher


def test_event_store_pops_events_once(f_store: EventStore, f_event: ZoneTouched) -> None:
    first = f_store.pop_events()
    second = f_store.pop_events()

    assert (first, second) == ((f_event,), ())


def test_dispatcher_delivers_to_matching_handlers_despite_failure(
    f_dispatcher: EventDispatcher,
    f_event: ZoneTouched,
    f_received: list[DomainEvent],
    f_ignored: list[DomainEvent],
) -> None:
    f_dispatcher.dispatch([f_event])

    assert (f_received, f_ignored) == ([f_event], [])
