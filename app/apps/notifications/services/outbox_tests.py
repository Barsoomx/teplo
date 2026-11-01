from datetime import timedelta

import pytest

from apps.notifications.services.outbox import AlertOutbox, OutgoingMessage
from apps.notifications.stubs import MonotonicClockStub


@pytest.fixture
def f_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=100.0)


@pytest.fixture
def f_outbox(f_clock: MonotonicClockStub) -> AlertOutbox:
    outbox = AlertOutbox(clock=f_clock, lifetime=timedelta(hours=1))
    outbox.put('Кухня: нет связи с термостатом с 20:24')
    f_clock.advance(60)
    outbox.put('Кухня: термостат снова на связи')
    return outbox


@pytest.fixture
def f_aged_outbox(f_outbox: AlertOutbox, f_clock: MonotonicClockStub) -> AlertOutbox:
    f_clock.advance(3600 - 59)
    return f_outbox


def test_messages_wait_in_order(f_outbox: AlertOutbox) -> None:
    assert f_outbox.pending() == (
        OutgoingMessage(number=1, text='Кухня: нет связи с термостатом с 20:24', queued_at=100.0),
        OutgoingMessage(number=2, text='Кухня: термостат снова на связи', queued_at=160.0),
    )


def test_remove_drops_only_that_message(f_outbox: AlertOutbox) -> None:
    f_outbox.remove(f_outbox.pending()[0])

    assert [message.number for message in f_outbox.pending()] == [2]


def test_messages_older_than_their_lifetime_expire(f_aged_outbox: AlertOutbox) -> None:
    expired = f_aged_outbox.drop_expired()

    assert (
        [message.number for message in expired],
        [message.number for message in f_aged_outbox.pending()],
    ) == ([1], [2])
