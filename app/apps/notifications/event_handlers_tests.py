from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from pytest_django import Settings

from apps.devices.domain.kinds import DeviceKind
from apps.notifications.domain.alerts import AlertKind
from apps.notifications.domain.events import AlertCleared, AlertRaised, TeploResumed
from apps.notifications.domain.outage import Outage
from apps.notifications.event_handlers import queue_alert_cleared, queue_alert_raised, queue_teplo_resumed
from apps.notifications.services.outbox import AlertOutbox, alert_outbox

SINCE = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
AT = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)


def _drain(outbox: AlertOutbox) -> None:
    for message in outbox.pending():
        outbox.remove(message)


def _texts(outbox: AlertOutbox) -> list[str]:
    return [message.text for message in outbox.pending()]


@pytest.fixture
def f_outbox() -> Iterator[AlertOutbox]:
    _drain(alert_outbox)
    yield alert_outbox
    _drain(alert_outbox)


@pytest.fixture
def english_alerts(settings: Settings) -> Settings:
    settings.TEPLO_ALERT_LANGUAGE = 'en'
    return settings


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
    return TeploResumed(
        outage=Outage(began_at=SINCE, ended_at=datetime(2026, 10, 31, 17, 33, tzinfo=UTC)),
        occurred_at=AT,
    )


def test_raised_alert_is_queued_in_russian(f_outbox: AlertOutbox, f_raised: AlertRaised) -> None:
    queue_alert_raised(f_raised)

    assert _texts(f_outbox) == ['Кухня: нет связи с термостатом с 20:24']


def test_cleared_alert_is_queued_in_russian(f_outbox: AlertOutbox, f_cleared: AlertCleared) -> None:
    queue_alert_cleared(f_cleared)

    assert _texts(f_outbox) == ['Кухня: термостат снова работает по расписанию']


def test_outage_is_queued_in_russian(f_outbox: AlertOutbox, f_resumed: TeploResumed) -> None:
    queue_teplo_resumed(f_resumed)

    assert _texts(f_outbox) == ['teplo снова работает. Перерыв 20:24–20:33 (9 мин)']


@pytest.mark.usefixtures('english_alerts')
def test_alert_language_comes_from_settings(f_outbox: AlertOutbox, f_raised: AlertRaised) -> None:
    queue_alert_raised(f_raised)

    assert _texts(f_outbox) == ['Кухня: no connection to the thermostat since 20:24']
