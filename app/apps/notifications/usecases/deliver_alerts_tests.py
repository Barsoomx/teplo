from datetime import timedelta

import pytest
from structlog.testing import capture_logs

from apps.core.stubs import MonotonicClockStub
from apps.notifications.domain.alerts import AlertKey, AlertKind, AlertTransition
from apps.notifications.domain.errors import AlertDeliveryError, AlertRejectedError
from apps.notifications.services.outbox import AlertOutbox
from apps.notifications.stubs import AlertSenderStub
from apps.notifications.usecases.deliver_alerts import (
    DeliverAlertsInputDTO,
    DeliverAlertsOutputDTO,
    DeliverAlertsUseCase,
)

RESUMED = 'teplo снова работает. Перерыв 20:24–20:33 (9 мин)'
LOST = 'Кухня: нет связи с термостатом с 20:24'
FOUND = 'Кухня: термостат снова на связи'
KITCHEN_LOST = AlertKey(kind=AlertKind.DEVICE_LOST, subject='thermostat-kitchen')
TIMED_OUT = 'telegram unreachable: timed out'


@pytest.fixture
def f_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def f_outbox(f_clock: MonotonicClockStub) -> AlertOutbox:
    outbox = AlertOutbox(clock=f_clock, lifetime=timedelta(hours=1))
    outbox.put(RESUMED)
    outbox.put(LOST, alert=KITCHEN_LOST, transition=AlertTransition.RAISED)
    outbox.put(FOUND, alert=KITCHEN_LOST, transition=AlertTransition.CLEARED)
    return outbox


@pytest.fixture
def f_stale_outbox(f_outbox: AlertOutbox, f_clock: MonotonicClockStub) -> AlertOutbox:
    f_clock.advance(3601)
    return f_outbox


@pytest.fixture
def f_lasting_alert_outbox(f_clock: MonotonicClockStub) -> AlertOutbox:
    outbox = AlertOutbox(clock=f_clock, lifetime=timedelta(hours=1))
    outbox.put(LOST, alert=KITCHEN_LOST, transition=AlertTransition.RAISED)
    return outbox


@pytest.fixture
def f_sender() -> AlertSenderStub:
    return AlertSenderStub(errors={})


@pytest.fixture
def f_offline_sender() -> AlertSenderStub:
    return AlertSenderStub(errors={RESUMED: AlertDeliveryError(TIMED_OUT)})


@pytest.fixture
def f_refusing_sender() -> AlertSenderStub:
    return AlertSenderStub(errors={LOST: AlertRejectedError('telegram answered: HTTP Error 400: Bad Request')})


@pytest.fixture
def f_use_case(f_outbox: AlertOutbox, f_sender: AlertSenderStub) -> DeliverAlertsUseCase:
    return DeliverAlertsUseCase(outbox=f_outbox, sender=f_sender)


@pytest.fixture
def f_offline_use_case(f_outbox: AlertOutbox, f_offline_sender: AlertSenderStub) -> DeliverAlertsUseCase:
    return DeliverAlertsUseCase(outbox=f_outbox, sender=f_offline_sender)


@pytest.fixture
def f_refusing_use_case(f_outbox: AlertOutbox, f_refusing_sender: AlertSenderStub) -> DeliverAlertsUseCase:
    return DeliverAlertsUseCase(outbox=f_outbox, sender=f_refusing_sender)


@pytest.fixture
def f_stale_use_case(f_stale_outbox: AlertOutbox, f_sender: AlertSenderStub) -> DeliverAlertsUseCase:
    return DeliverAlertsUseCase(outbox=f_stale_outbox, sender=f_sender)


@pytest.fixture
def f_reconnected_use_case(
    f_lasting_alert_outbox: AlertOutbox,
    f_clock: MonotonicClockStub,
    f_sender: AlertSenderStub,
) -> DeliverAlertsUseCase:
    offline = DeliverAlertsUseCase(
        outbox=f_lasting_alert_outbox,
        sender=AlertSenderStub(errors={LOST: AlertDeliveryError(TIMED_OUT)}),
    )
    for _minute in range(120):
        offline.execute(DeliverAlertsInputDTO())
        f_clock.advance(60)
    return DeliverAlertsUseCase(outbox=f_lasting_alert_outbox, sender=f_sender)


def test_queued_messages_go_out_in_order(f_use_case: DeliverAlertsUseCase, f_sender: AlertSenderStub) -> None:
    output = f_use_case.execute(DeliverAlertsInputDTO())

    assert (output, f_sender.sent) == (DeliverAlertsOutputDTO(sent=3, dropped=0, pending=0), [RESUMED, LOST, FOUND])


def test_failed_send_keeps_the_queue_for_the_next_run(
    f_offline_use_case: DeliverAlertsUseCase,
    f_offline_sender: AlertSenderStub,
) -> None:
    with capture_logs() as logs:
        output = f_offline_use_case.execute(DeliverAlertsInputDTO())

    assert (output, f_offline_sender.attempts, logs) == (
        DeliverAlertsOutputDTO(sent=0, dropped=0, pending=3),
        [RESUMED],
        [
            {
                'event': 'alert_delivery_failed',
                'log_level': 'warning',
                'error_code': 'alert_delivery_failed',
                'detail': 'telegram unreachable: timed out',
                'pending': 3,
            },
        ],
    )


def test_refused_message_is_dropped_and_the_rest_go_out(
    f_refusing_use_case: DeliverAlertsUseCase,
    f_refusing_sender: AlertSenderStub,
) -> None:
    output = f_refusing_use_case.execute(DeliverAlertsInputDTO())

    assert (output, f_refusing_sender.sent) == (DeliverAlertsOutputDTO(sent=2, dropped=1, pending=0), [RESUMED, FOUND])


def test_messages_past_their_lifetime_are_dropped_unsent(
    f_stale_use_case: DeliverAlertsUseCase,
    f_sender: AlertSenderStub,
) -> None:
    output = f_stale_use_case.execute(DeliverAlertsInputDTO())

    assert (output, f_sender.attempts) == (DeliverAlertsOutputDTO(sent=0, dropped=3, pending=0), [])


def test_alert_outlasting_a_two_hour_telegram_outage_is_delivered(
    f_reconnected_use_case: DeliverAlertsUseCase,
    f_sender: AlertSenderStub,
) -> None:
    output = f_reconnected_use_case.execute(DeliverAlertsInputDTO())

    assert (output, f_sender.sent) == (DeliverAlertsOutputDTO(sent=1, dropped=0, pending=0), [LOST])
