from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from django.utils import translation

from apps.devices.domain.kinds import DeviceKind
from apps.notifications.domain.alerts import AlertKind, AlertTransition
from apps.notifications.domain.outage import Outage
from apps.notifications.services.messages import (
    alert_text,
    alert_text_from_payload,
    delivery_check_text,
    outage_text,
)

SINCE = datetime(2026, 10, 31, 17, 24, 30, tzinfo=UTC)
AT = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)
PAYLOAD = {
    'kind': 'device_lost',
    'subject': 'thermostat-kitchen',
    'name': 'Кухня',
    'device_kind': 'thermostat',
    'since': '2026-10-31T17:24:30Z',
}


@pytest.fixture
def russian() -> Iterator[None]:
    with translation.override('ru'):
        yield


@pytest.fixture
def english() -> Iterator[None]:
    with translation.override('en'):
        yield


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('kind', 'transition', 'device_kind', 'expected'),
    [
        (
            AlertKind.DEVICE_LOST,
            AlertTransition.RAISED,
            DeviceKind.THERMOSTAT,
            'Кухня: нет связи с термостатом с 20:24',
        ),
        (AlertKind.DEVICE_LOST, AlertTransition.RAISED, DeviceKind.PLUG, 'Кухня: нет связи с розеткой с 20:24'),
        (AlertKind.DEVICE_LOST, AlertTransition.RAISED, DeviceKind.TEMPERATURE_SENSOR, 'Кухня: нет связи с 20:24'),
        (AlertKind.DEVICE_LOST, AlertTransition.CLEARED, DeviceKind.THERMOSTAT, 'Кухня: термостат снова на связи'),
        (AlertKind.DEVICE_LOST, AlertTransition.CLEARED, DeviceKind.PLUG, 'Кухня: розетка снова на связи'),
        (AlertKind.DEVICE_LOST, AlertTransition.CLEARED, DeviceKind.TEMPERATURE_SENSOR, 'Кухня: снова на связи'),
        (
            AlertKind.ZONE_OFF,
            AlertTransition.RAISED,
            DeviceKind.THERMOSTAT,
            'Кухня: термостат выключен с 20:24, хотя по расписанию должен греть',
        ),
        (
            AlertKind.ZONE_OFF,
            AlertTransition.CLEARED,
            DeviceKind.THERMOSTAT,
            'Кухня: термостат снова работает по расписанию',
        ),
    ],
)
def test_alert_reads_in_russian(
    kind: AlertKind,
    transition: AlertTransition,
    device_kind: DeviceKind,
    expected: str,
) -> None:
    assert alert_text(kind, transition, 'Кухня', device_kind, SINCE, AT) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('kind', 'device_kind', 'expected'),
    [
        (AlertKind.DEVICE_LOST, DeviceKind.THERMOSTAT, 'Кухня: нет связи с термостатом'),
        (AlertKind.DEVICE_LOST, DeviceKind.PLUG, 'Кухня: нет связи с розеткой'),
        (AlertKind.DEVICE_LOST, DeviceKind.TEMPERATURE_SENSOR, 'Кухня: нет связи'),
        (AlertKind.ZONE_OFF, DeviceKind.THERMOSTAT, 'Кухня: термостат выключен, хотя по расписанию должен греть'),
    ],
)
def test_alert_without_a_trusted_time_names_none(kind: AlertKind, device_kind: DeviceKind, expected: str) -> None:
    assert alert_text(kind, AlertTransition.RAISED, 'Кухня', device_kind, None, AT) == expected


@pytest.mark.usefixtures('russian')
def test_alert_since_an_earlier_day_names_the_date() -> None:
    text = alert_text(
        AlertKind.DEVICE_LOST,
        AlertTransition.RAISED,
        'Кухня',
        DeviceKind.THERMOSTAT,
        SINCE,
        AT + timedelta(days=1),
    )

    assert text == 'Кухня: нет связи с термостатом с 31 октября 20:24'


@pytest.mark.usefixtures('english')
def test_alert_follows_the_active_language() -> None:
    text = alert_text(AlertKind.DEVICE_LOST, AlertTransition.RAISED, 'Kitchen', DeviceKind.THERMOSTAT, SINCE, AT)

    assert text == 'Kitchen: no connection to the thermostat since 20:24'


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('began_at', 'ended_at', 'expected'),
    [
        (
            datetime(2026, 10, 31, 17, 24, 50, tzinfo=UTC),
            datetime(2026, 10, 31, 17, 33, 10, tzinfo=UTC),
            'teplo снова работает. Перерыв 20:24–20:33 (9 мин)',
        ),
        (
            datetime(2026, 10, 31, 16, 0, tzinfo=UTC),
            datetime(2026, 10, 31, 17, 5, tzinfo=UTC),
            'teplo снова работает. Перерыв 19:00–20:05 (1 ч 5 мин)',
        ),
        (
            datetime(2026, 10, 31, 15, 0, tzinfo=UTC),
            datetime(2026, 10, 31, 17, 0, tzinfo=UTC),
            'teplo снова работает. Перерыв 18:00–20:00 (2 ч)',
        ),
        (
            datetime(2026, 10, 31, 20, 50, tzinfo=UTC),
            datetime(2026, 10, 31, 21, 10, tzinfo=UTC),
            'teplo снова работает. Перерыв 31 октября 23:50–00:10 (20 мин)',
        ),
    ],
)
def test_outage_reads_as_a_local_interval(began_at: datetime, ended_at: datetime, expected: str) -> None:
    assert outage_text(Outage(began_at=began_at, ended_at=ended_at)) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('transition', 'expected'),
    [
        (AlertTransition.RAISED, 'Кухня: нет связи с термостатом с 20:24'),
        (AlertTransition.CLEARED, 'Кухня: термостат снова на связи'),
    ],
)
def test_journaled_alert_reads_like_the_message(transition: AlertTransition, expected: str) -> None:
    assert alert_text_from_payload(transition, PAYLOAD, AT) == expected


@pytest.mark.usefixtures('russian')
def test_journaled_alert_without_a_time_reads_without_one() -> None:
    text = alert_text_from_payload(AlertTransition.RAISED, {**PAYLOAD, 'since': None}, AT)

    assert text == 'Кухня: нет связи с термостатом'


@pytest.mark.parametrize(
    'payload',
    [
        {},
        {**PAYLOAD, 'kind': 'meteor'},
        {**PAYLOAD, 'device_kind': 'toaster'},
        {**PAYLOAD, 'since': 'yesterday'},
        {**PAYLOAD, 'since': '2026-10-31T17:24:30'},
    ],
)
def test_unreadable_journal_payload_gives_no_text(payload: dict[str, object]) -> None:
    assert alert_text_from_payload(AlertTransition.RAISED, payload, AT) is None


@pytest.mark.usefixtures('russian')
def test_delivery_check_reads_in_russian() -> None:
    assert delivery_check_text() == 'teplo: проверка связи. Оповещения будут приходить в этот чат'
