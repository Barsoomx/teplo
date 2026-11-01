from datetime import timedelta

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.notifications.domain.alerts import AlertKey, AlertKind, AlertTransition
from apps.notifications.services.outbox import AlertOutbox, OutgoingMessage

KITCHEN_LOST = AlertKey(kind=AlertKind.DEVICE_LOST, subject='thermostat-kitchen')
LOST = 'Кухня: нет связи с термостатом с 20:24'
FOUND = 'Кухня: термостат снова на связи'
RESUMED = 'teplo снова работает. Перерыв 20:24–20:33 (9 мин)'
HOUR = 3600.0


def _numbers(messages: tuple[OutgoingMessage, ...]) -> list[int]:
    return [message.number for message in messages]


@pytest.fixture
def f_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=100.0)


@pytest.fixture
def f_outbox(f_clock: MonotonicClockStub) -> AlertOutbox:
    outbox = AlertOutbox(clock=f_clock, lifetime=timedelta(hours=1))
    outbox.put(LOST, alert=KITCHEN_LOST, transition=AlertTransition.RAISED)
    f_clock.advance(60)
    outbox.put(RESUMED)
    return outbox


@pytest.fixture
def f_aged_outbox(f_outbox: AlertOutbox, f_clock: MonotonicClockStub) -> AlertOutbox:
    f_clock.advance(HOUR + 1)
    return f_outbox


@pytest.fixture
def f_long_outbox(f_outbox: AlertOutbox, f_clock: MonotonicClockStub) -> AlertOutbox:
    f_clock.advance(2 * HOUR)
    return f_outbox


@pytest.fixture
def f_recovered_outbox(f_outbox: AlertOutbox, f_clock: MonotonicClockStub) -> AlertOutbox:
    f_clock.advance(HOUR)
    f_outbox.put(FOUND, alert=KITCHEN_LOST, transition=AlertTransition.CLEARED)
    return f_outbox


@pytest.fixture
def f_recovered_hour_ago_outbox(f_recovered_outbox: AlertOutbox, f_clock: MonotonicClockStub) -> AlertOutbox:
    f_clock.advance(HOUR)
    return f_recovered_outbox


@pytest.fixture
def f_recovered_long_ago_outbox(f_recovered_outbox: AlertOutbox, f_clock: MonotonicClockStub) -> AlertOutbox:
    f_clock.advance(HOUR + 1)
    return f_recovered_outbox


@pytest.fixture
def f_announced_outbox(f_clock: MonotonicClockStub) -> AlertOutbox:
    outbox = AlertOutbox(clock=f_clock, lifetime=timedelta(hours=1))
    outbox.put(FOUND, alert=KITCHEN_LOST, transition=AlertTransition.CLEARED)
    f_clock.advance(HOUR + 1)
    return outbox


def test_messages_wait_in_order(f_outbox: AlertOutbox) -> None:
    assert f_outbox.pending() == (
        OutgoingMessage(
            number=1,
            text=LOST,
            queued_at=100.0,
            alert=KITCHEN_LOST,
            transition=AlertTransition.RAISED,
        ),
        OutgoingMessage(number=2, text=RESUMED, queued_at=160.0),
    )


def test_remove_drops_only_that_message(f_outbox: AlertOutbox) -> None:
    f_outbox.remove(f_outbox.pending()[0])

    assert _numbers(f_outbox.pending()) == [2]


def test_messages_older_than_their_lifetime_expire(f_aged_outbox: AlertOutbox) -> None:
    expired = f_aged_outbox.drop_expired()

    assert (_numbers(expired), _numbers(f_aged_outbox.pending())) == ([2], [1])


def test_raised_alert_waits_as_long_as_it_lasts(f_long_outbox: AlertOutbox) -> None:
    expired = f_long_outbox.drop_expired()

    assert (_numbers(expired), _numbers(f_long_outbox.pending())) == ([2], [1])


def test_alert_and_its_recovery_wait_an_hour_after_the_recovery(f_recovered_hour_ago_outbox: AlertOutbox) -> None:
    expired = f_recovered_hour_ago_outbox.drop_expired()

    assert (_numbers(expired), _numbers(f_recovered_hour_ago_outbox.pending())) == ([2], [1, 3])


def test_alert_and_its_recovery_expire_together(f_recovered_long_ago_outbox: AlertOutbox) -> None:
    expired = f_recovered_long_ago_outbox.drop_expired()

    assert (_numbers(expired), f_recovered_long_ago_outbox.pending()) == ([1, 2, 3], ())


def test_recovery_of_an_alert_already_sent_expires_after_an_hour(f_announced_outbox: AlertOutbox) -> None:
    expired = f_announced_outbox.drop_expired()

    assert (_numbers(expired), f_announced_outbox.pending()) == ([1], ())
