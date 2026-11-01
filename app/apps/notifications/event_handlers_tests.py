from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from apps.devices.domain.kinds import DeviceKind
from apps.notifications.domain.alerts import AlertKey, AlertKind, AlertTransition
from apps.notifications.domain.events import AlertCleared, AlertRaised, TeploResumed
from apps.notifications.domain.outage import Outage
from apps.notifications.event_handlers import queue_alert_cleared, queue_alert_raised, queue_teplo_resumed
from apps.notifications.services.messages import AlertContent, OutageContent
from apps.notifications.services.outbox import AlertOutbox, MessageContent, alert_outbox

SINCE = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
AT = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)
OUTAGE = Outage(began_at=SINCE, ended_at=datetime(2026, 10, 31, 17, 33, tzinfo=UTC))


def _drain(outbox: AlertOutbox) -> None:
    for message in outbox.pending():
        outbox.remove(message)


def _contents(outbox: AlertOutbox) -> list[MessageContent]:
    return [message.content for message in outbox.pending()]


@pytest.fixture
def f_outbox() -> Iterator[AlertOutbox]:
    _drain(alert_outbox)
    yield alert_outbox
    _drain(alert_outbox)


@pytest.fixture
def f_raised() -> AlertRaised:
    return AlertRaised(
        kind=AlertKind.DEVICE_LOST,
        subject='thermostat-kitchen',
        name='Кухня',
        device_kind=DeviceKind.THERMOSTAT,
        since=SINCE,
        occurred_at=AT,
    )


@pytest.fixture
def f_plug_raised() -> AlertRaised:
    return AlertRaised(
        kind=AlertKind.DEVICE_LOST,
        subject='plug-boiler-1f',
        name='Бойлер 1 этаж',
        device_kind=DeviceKind.PLUG,
        since=SINCE,
        occurred_at=AT,
    )


@pytest.fixture
def f_cleared() -> AlertCleared:
    return AlertCleared(
        kind=AlertKind.ZONE_OFF,
        subject='kitchen',
        name='Кухня',
        device_kind=DeviceKind.THERMOSTAT,
        since=SINCE,
        occurred_at=AT,
    )


@pytest.fixture
def f_resumed() -> TeploResumed:
    return TeploResumed(outage=OUTAGE, occurred_at=AT)


def test_raised_alert_is_queued_to_be_written_when_sent(f_outbox: AlertOutbox, f_raised: AlertRaised) -> None:
    queue_alert_raised(f_raised)

    assert _contents(f_outbox) == [
        AlertContent(
            kind=AlertKind.DEVICE_LOST,
            transition=AlertTransition.RAISED,
            name='Кухня',
            device_kind=DeviceKind.THERMOSTAT,
            since=SINCE,
        ),
    ]


def test_raised_plug_alert_keeps_the_kind_of_device(f_outbox: AlertOutbox, f_plug_raised: AlertRaised) -> None:
    queue_alert_raised(f_plug_raised)

    assert _contents(f_outbox) == [
        AlertContent(
            kind=AlertKind.DEVICE_LOST,
            transition=AlertTransition.RAISED,
            name='Бойлер 1 этаж',
            device_kind=DeviceKind.PLUG,
            since=SINCE,
        ),
    ]


def test_cleared_alert_is_queued_to_be_written_when_sent(f_outbox: AlertOutbox, f_cleared: AlertCleared) -> None:
    queue_alert_cleared(f_cleared)

    assert _contents(f_outbox) == [
        AlertContent(
            kind=AlertKind.ZONE_OFF,
            transition=AlertTransition.CLEARED,
            name='Кухня',
            device_kind=DeviceKind.THERMOSTAT,
            since=SINCE,
        ),
    ]


def test_outage_is_queued_to_be_written_when_sent(f_outbox: AlertOutbox, f_resumed: TeploResumed) -> None:
    queue_teplo_resumed(f_resumed)

    assert _contents(f_outbox) == [OutageContent(outage=OUTAGE)]


def test_alerts_are_queued_under_their_keys(
    f_outbox: AlertOutbox,
    f_raised: AlertRaised,
    f_cleared: AlertCleared,
    f_resumed: TeploResumed,
) -> None:
    queue_alert_raised(f_raised)
    queue_alert_cleared(f_cleared)
    queue_teplo_resumed(f_resumed)

    assert [(message.alert, message.transition) for message in f_outbox.pending()] == [
        (AlertKey(kind=AlertKind.DEVICE_LOST, subject='thermostat-kitchen'), AlertTransition.RAISED),
        (AlertKey(kind=AlertKind.ZONE_OFF, subject='kitchen'), AlertTransition.CLEARED),
        (None, None),
    ]
