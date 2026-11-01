from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.kinds import DeviceKind
from apps.notifications.domain.alerts import AlertKey, AlertKind, AlertTransition
from apps.notifications.domain.errors import AlertDeliveryError, AlertRejectedError
from apps.notifications.domain.outage import Outage
from apps.notifications.services.messages import AlertContent, OutageContent
from apps.notifications.services.outbox import AlertOutbox
from apps.notifications.stubs import AlertSenderStub
from apps.notifications.usecases.deliver_alerts import (
    DeliverAlertsInputDTO,
    DeliverAlertsOutputDTO,
    DeliverAlertsUseCase,
)

NOW = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)
SINCE = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
KITCHEN_LOST = AlertKey(kind=AlertKind.DEVICE_LOST, subject='thermostat-kitchen')
BOILER_LOST = AlertKey(kind=AlertKind.DEVICE_LOST, subject='plug-boiler-1f')
OUTAGE_CONTENT = OutageContent(outage=Outage(began_at=SINCE, ended_at=datetime(2026, 10, 31, 17, 33, tzinfo=UTC)))
LOST_CONTENT = AlertContent(
    kind=AlertKind.DEVICE_LOST,
    transition=AlertTransition.RAISED,
    name='Кухня',
    device_kind=DeviceKind.THERMOSTAT,
    since=SINCE,
)
FOUND_CONTENT = AlertContent(
    kind=AlertKind.DEVICE_LOST,
    transition=AlertTransition.CLEARED,
    name='Кухня',
    device_kind=DeviceKind.THERMOSTAT,
    since=SINCE,
)
BOILER_LOST_CONTENT = AlertContent(
    kind=AlertKind.DEVICE_LOST,
    transition=AlertTransition.RAISED,
    name='Бойлер 1 этаж',
    device_kind=DeviceKind.PLUG,
    since=datetime(2026, 11, 1, 18, 15, tzinfo=UTC),
)
RESUMED = 'teplo снова работает. Перерыв 20:24–20:33 (9 мин)'
LOST = 'Кухня: нет связи с термостатом с 20:24'
FOUND = 'Кухня: термостат снова на связи'
TIMED_OUT = 'telegram unreachable: timed out'


@pytest.fixture
def f_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=0.0)


@pytest.fixture
def f_outbox(f_clock: MonotonicClockStub) -> AlertOutbox:
    outbox = AlertOutbox(clock=f_clock, lifetime=timedelta(hours=1))
    outbox.put(OUTAGE_CONTENT)
    outbox.put(LOST_CONTENT, alert=KITCHEN_LOST, transition=AlertTransition.RAISED)
    outbox.put(FOUND_CONTENT, alert=KITCHEN_LOST, transition=AlertTransition.CLEARED)
    return outbox


@pytest.fixture
def f_stale_outbox(f_outbox: AlertOutbox, f_clock: MonotonicClockStub) -> AlertOutbox:
    f_clock.advance(3601)
    return f_outbox


@pytest.fixture
def lasting_alert_outbox(f_clock: MonotonicClockStub) -> AlertOutbox:
    outbox = AlertOutbox(clock=f_clock, lifetime=timedelta(hours=1))
    outbox.put(LOST_CONTENT, alert=KITCHEN_LOST, transition=AlertTransition.RAISED)
    return outbox


@pytest.fixture
def boiler_outbox(f_clock: MonotonicClockStub) -> AlertOutbox:
    outbox = AlertOutbox(clock=f_clock, lifetime=timedelta(hours=1))
    outbox.put(BOILER_LOST_CONTENT, alert=BOILER_LOST, transition=AlertTransition.RAISED)
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
    return DeliverAlertsUseCase(outbox=f_outbox, sender=f_sender, language='ru')


@pytest.fixture
def f_english_use_case(f_outbox: AlertOutbox, f_sender: AlertSenderStub) -> DeliverAlertsUseCase:
    return DeliverAlertsUseCase(outbox=f_outbox, sender=f_sender, language='en')


@pytest.fixture
def f_offline_use_case(f_outbox: AlertOutbox, f_offline_sender: AlertSenderStub) -> DeliverAlertsUseCase:
    return DeliverAlertsUseCase(outbox=f_outbox, sender=f_offline_sender, language='ru')


@pytest.fixture
def f_refusing_use_case(f_outbox: AlertOutbox, f_refusing_sender: AlertSenderStub) -> DeliverAlertsUseCase:
    return DeliverAlertsUseCase(outbox=f_outbox, sender=f_refusing_sender, language='ru')


@pytest.fixture
def f_stale_use_case(f_stale_outbox: AlertOutbox, f_sender: AlertSenderStub) -> DeliverAlertsUseCase:
    return DeliverAlertsUseCase(outbox=f_stale_outbox, sender=f_sender, language='ru')


@pytest.fixture
def f_reconnected_use_case(
    lasting_alert_outbox: AlertOutbox,
    f_clock: MonotonicClockStub,
    f_sender: AlertSenderStub,
) -> DeliverAlertsUseCase:
    offline = DeliverAlertsUseCase(
        outbox=lasting_alert_outbox,
        sender=AlertSenderStub(errors={LOST: AlertDeliveryError(TIMED_OUT)}),
        language='ru',
    )
    for minute in range(120):
        offline.execute(DeliverAlertsInputDTO(now=NOW + timedelta(minutes=minute)))
        f_clock.advance(60)
    return DeliverAlertsUseCase(outbox=lasting_alert_outbox, sender=f_sender, language='ru')


@pytest.fixture
def f_boiler_use_case(boiler_outbox: AlertOutbox, f_sender: AlertSenderStub) -> DeliverAlertsUseCase:
    return DeliverAlertsUseCase(outbox=boiler_outbox, sender=f_sender, language='ru')


def test_queued_messages_go_out_in_order(f_use_case: DeliverAlertsUseCase, f_sender: AlertSenderStub) -> None:
    output = f_use_case.execute(DeliverAlertsInputDTO(now=NOW))

    assert (output, f_sender.sent) == (DeliverAlertsOutputDTO(sent=3, dropped=0, pending=0), [RESUMED, LOST, FOUND])


def test_messages_are_written_in_the_alert_language(
    f_english_use_case: DeliverAlertsUseCase,
    f_sender: AlertSenderStub,
) -> None:
    f_english_use_case.execute(DeliverAlertsInputDTO(now=NOW))

    assert f_sender.sent == [
        'teplo is running again. Outage 20:24–20:33 (9 min)',
        'Кухня: no connection to the thermostat since 20:24',
        'Кухня: the thermostat is back online',
    ]


@pytest.mark.parametrize(
    ('now', 'expected'),
    [
        (datetime(2026, 11, 1, 18, 28, tzinfo=UTC), 'Бойлер 1 этаж: нет связи с розеткой с 21:15'),
        (datetime(2026, 11, 3, 19, 0, tzinfo=UTC), 'Бойлер 1 этаж: нет связи с розеткой с 1 ноября 21:15'),
    ],
    ids=['sent_the_same_day', 'sent_two_days_later'],
)
def test_alert_is_dated_against_the_moment_it_is_sent(
    f_boiler_use_case: DeliverAlertsUseCase,
    f_sender: AlertSenderStub,
    now: datetime,
    expected: str,
) -> None:
    f_boiler_use_case.execute(DeliverAlertsInputDTO(now=now))

    assert f_sender.sent == [expected]


def test_failed_send_keeps_the_queue_for_the_next_run(
    f_offline_use_case: DeliverAlertsUseCase,
    f_offline_sender: AlertSenderStub,
) -> None:
    with capture_logs() as logs:
        output = f_offline_use_case.execute(DeliverAlertsInputDTO(now=NOW))

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
    output = f_refusing_use_case.execute(DeliverAlertsInputDTO(now=NOW))

    assert (output, f_refusing_sender.sent) == (DeliverAlertsOutputDTO(sent=2, dropped=1, pending=0), [RESUMED, FOUND])


def test_messages_past_their_lifetime_are_dropped_unsent(
    f_stale_use_case: DeliverAlertsUseCase,
    f_sender: AlertSenderStub,
) -> None:
    output = f_stale_use_case.execute(DeliverAlertsInputDTO(now=NOW + timedelta(hours=1)))

    assert (output, f_sender.attempts) == (DeliverAlertsOutputDTO(sent=0, dropped=3, pending=0), [])


def test_alert_outlasting_a_two_hour_telegram_outage_is_delivered(
    f_reconnected_use_case: DeliverAlertsUseCase,
    f_sender: AlertSenderStub,
) -> None:
    output = f_reconnected_use_case.execute(DeliverAlertsInputDTO(now=NOW + timedelta(hours=2)))

    assert (output, f_sender.sent) == (DeliverAlertsOutputDTO(sent=1, dropped=0, pending=0), [LOST])
