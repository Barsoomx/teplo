from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.utils import translation
from freezegun import freeze_time

from apps.core.domain.errors import DomainError, SecretMissingError
from apps.devices.domain.errors import (
    DeviceConfigError,
    DeviceNotFoundError,
    DeviceProtocolError,
    DeviceUnavailableError,
    DriverNotImplementedError,
)
from apps.devices.domain.kinds import DeviceKind
from apps.panel.templatetags.panel_format import (
    alert_text,
    clock_note,
    command_summary,
    day_word,
    failure_note,
    icon,
    local_time,
)
from apps.panel.usecases.get_journal_timeline import TimelineEntry
from apps.system.domain.clock import ClockStatusCode

MOSCOW = ZoneInfo('Europe/Moscow')
ALERT_PAYLOAD = {
    'kind': 'device_lost',
    'subject': 'thermostat-kitchen',
    'name': 'Кухня',
    'device_kind': 'thermostat',
    'since': '2026-10-31T17:24:00Z',
}


@pytest.fixture
def russian() -> Iterator[None]:
    with translation.override('ru'):
        yield


def test_icon_references_the_sprite() -> None:
    assert icon('flame') == (
        '<svg class="icon" aria-hidden="true" focusable="false"><use href="/static/panel/icons.svg#flame"></use></svg>'
    )


@pytest.mark.parametrize(
    ('change', 'expected'),
    [
        ({'power': True, 'mode': 'hold', 'setpoint': '18.0'}, 'turn on, manual mode, set to 18°'),
        ({'power': None, 'mode': None, 'setpoint': '21.5'}, 'set to 21.5°'),
        ({'power': False, 'mode': None, 'setpoint': None}, 'turn off'),
    ],
)
def test_command_summary_lists_the_changed_settings(change: dict[str, object], expected: str) -> None:
    assert command_summary(change) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('error', 'expected'),
    [
        (DeviceUnavailableError('timed out'), 'Нет связи с термостатом'),
        (DeviceProtocolError('payload without dps'), 'Термостат прислал неожиданный ответ'),
        (DriverNotImplementedError('unsupported'), 'teplo пока не умеет управлять этим устройством'),
        (DeviceNotFoundError('thermostat-attic'), 'Устройства нет в настройках'),
        (SecretMissingError('thermostat-attic'), 'В файле секретов нет адреса и ключа этого термостата'),
        (DeviceConfigError('no address'), 'В файле секретов у этого термостата неверный адрес или протокол'),
        (DomainError('boom'), 'Неизвестная ошибка'),
    ],
)
def test_failure_note_names_the_cause_by_error_code(error: DomainError, expected: str) -> None:
    assert failure_note(error.error_code, error.detail) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('error', 'expected'),
    [
        (DeviceUnavailableError('timed out'), 'Нет связи с розеткой'),
        (DeviceProtocolError('payload without dps'), 'Розетка прислала неожиданный ответ'),
        (DriverNotImplementedError('unsupported'), 'teplo пока не умеет управлять этим устройством'),
        (SecretMissingError('plug-boiler-1f'), 'В файле секретов нет адреса и ключа этой розетки'),
        (DeviceConfigError('no address'), 'В файле секретов у этой розетки неверный адрес или протокол'),
    ],
)
def test_failure_note_names_the_plug_for_a_plug(error: DomainError, expected: str) -> None:
    assert failure_note(error.error_code, error.detail, device='plug') == expected


def test_failure_note_leaves_out_what_the_headline_says() -> None:
    assert failure_note('device_unavailable', 'timed out', omit='device_unavailable') == ''


def test_failure_note_without_code_is_generic() -> None:
    assert failure_note('', 'timed out', omit='device_unavailable') == 'Unknown error'


def test_failure_note_is_empty_without_failure() -> None:
    assert failure_note(None, None, omit='device_unavailable') == ''


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('code', 'expected'),
    [
        (ClockStatusCode.NTP_NOT_SYNCHRONIZED, ''),
        (ClockStatusCode.NTP_STATUS_MISSING, 'роутер не сообщает статус NTP'),
        (ClockStatusCode.NTP_STATUS_FAILED, 'не удалось узнать статус NTP у роутера'),
    ],
)
def test_clock_note_adds_what_the_headline_does_not_say(code: ClockStatusCode, expected: str) -> None:
    assert clock_note(code) == expected


def test_local_time_shows_journal_timestamps_in_house_time() -> None:
    assert local_time('2026-10-29T21:15:00Z') == '00:15'


@freeze_time('2026-10-29T17:00:00Z')
@pytest.mark.parametrize(
    ('moment', 'expected'),
    [
        (datetime(2026, 10, 29, 23, 30, tzinfo=MOSCOW), ''),
        (datetime(2026, 10, 30, 3, 30, tzinfo=MOSCOW), 'tomorrow'),
        (datetime(2026, 11, 6, 6, 30, tzinfo=MOSCOW), '6 November'),
    ],
)
def test_day_word_names_days_other_than_today(moment: datetime, expected: str) -> None:
    assert day_word(moment) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('kind', 'payload', 'expected'),
    [
        ('alert_raised', ALERT_PAYLOAD, 'Кухня: нет связи с термостатом с 20:24'),
        ('alert_raised', {**ALERT_PAYLOAD, 'since': None}, 'Кухня: нет связи с термостатом'),
        ('alert_cleared', ALERT_PAYLOAD, 'Кухня: термостат снова на связи'),
        ('alert_raised', {}, 'Кухня: неизвестное событие'),
    ],
)
def test_alert_text_reads_journaled_alerts_like_the_messages(
    kind: str,
    payload: dict[str, object],
    expected: str,
) -> None:
    entry = TimelineEntry(
        kind=kind,
        subject='thermostat-kitchen',
        subject_name='Кухня',
        device_kind=DeviceKind.THERMOSTAT,
        payload=payload,
        created_at=datetime(2026, 10, 31, 20, 40, tzinfo=MOSCOW),
    )

    assert alert_text(entry) == expected
