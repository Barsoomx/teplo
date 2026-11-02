from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import translation
from freezegun import freeze_time

from apps.core.domain.errors import DomainError, SecretMissingError
from apps.devices.domain.errors import (
    BrokerUnavailableError,
    DeviceConfigError,
    DeviceNotFoundError,
    DeviceProtocolError,
    DeviceUnavailableError,
    DriverNotImplementedError,
    SensorReadingError,
)
from apps.devices.domain.kinds import DeviceKind
from apps.panel.templatetags.panel_format import (
    air_control_note,
    air_control_text,
    alert_text,
    clock_note,
    command_summary,
    day_word,
    duration,
    failure_note,
    icon,
    local_time,
    optimal_start_text,
    outage_question,
    power_outage_text,
    second_floor_guard_text,
)
from apps.panel.usecases.get_journal_timeline import TimelineEntry
from apps.power.domain.outage import OutageCause, OutageRecord
from apps.system.domain.clock import ClockStatusCode

MOSCOW = ZoneInfo('Europe/Moscow')
CUT = datetime(2026, 10, 31, 20, 24, 40, tzinfo=MOSCOW)
OUTAGE = OutageRecord(
    id=7,
    began_at=CUT,
    ended_at=CUT + timedelta(minutes=8, seconds=25),
    cause=OutageCause.UNMARKED,
    cause_marked_at=None,
)
ALERT_PAYLOAD = {
    'kind': 'device_lost',
    'subject': 'thermostat-kitchen',
    'name': 'Кухня',
    'device_kind': 'thermostat',
    'since': '2026-10-31T17:24:00Z',
}
AIR_CONTROL_PAYLOAD: dict[str, object] = {
    'zone': 'kitchen',
    'air': '20.9',
    'target': '22.0',
    'mode': 'preheat',
    'state': 'heat',
    'previous_state': 'idle',
    'power': True,
    'setpoint': '30.0',
    'early_stop_offset': '0.30',
    'lead_minutes': 140,
}


def _entry(kind: str, payload: dict[str, object], name: str = 'Кухня') -> TimelineEntry:
    return TimelineEntry(
        kind=kind,
        subject='kitchen',
        subject_name=name,
        device_kind=None,
        payload=payload,
        created_at=datetime(2026, 10, 29, 7, 0, tzinfo=MOSCOW),
    )


@pytest.fixture
def russian() -> Iterator[None]:
    with translation.override('ru'):
        yield


@pytest.fixture
def english() -> Iterator[None]:
    with translation.override('en'):
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


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('error', 'expected'),
    [
        (DeviceUnavailableError('the node went offline'), 'Нет связи с датчиком'),
        (DeviceProtocolError('temperature: not a number'), 'Датчик прислал неожиданный ответ'),
        (SensorReadingError('temperature: the node reported nan'), 'Датчик не смог снять показания'),
        (BrokerUnavailableError('connection refused'), 'teplo не может подключиться к MQTT-брокеру'),
    ],
)
def test_failure_note_names_the_sensor_for_a_temperature_sensor(error: DomainError, expected: str) -> None:
    assert failure_note(error.error_code, error.detail, device='temperature_sensor') == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('error', 'expected'),
    [
        (DeviceUnavailableError('the node went offline'), 'Нет связи с фазометром'),
        (DeviceProtocolError('l1_current: not a number'), 'Фазометр прислал неожиданный ответ'),
        (SensorReadingError('l2_current: the node reported nan'), 'Фазометр не смог измерить ток фазы'),
        (BrokerUnavailableError('connection refused'), 'teplo не может подключиться к MQTT-брокеру'),
    ],
)
def test_failure_note_names_the_phase_meter_for_a_phase_meter(error: DomainError, expected: str) -> None:
    assert failure_note(error.error_code, error.detail, device='phase_meter') == expected


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
    ('lead', 'expected'),
    [(timedelta(hours=3), '3 ч'), (timedelta(hours=2, minutes=40), '2 ч 40 мин')],
    ids=['hours', 'hours_and_minutes'],
)
def test_duration_reads_a_lead_in_hours_and_minutes(lead: timedelta, expected: str) -> None:
    assert duration(lead) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        (AIR_CONTROL_PAYLOAD, 'Кухня: в режиме по воздуху включил бы обогрев (20,9° при цели 22,0°)'),
        (
            {
                **AIR_CONTROL_PAYLOAD,
                'air': '21.8',
                'mode': 'comfort',
                'state': 'idle',
                'previous_state': 'heat',
                'setpoint': '18.0',
            },
            'Кухня: в режиме по воздуху остановил бы обогрев (21,8° при цели 22,0°)',
        ),
        (
            {**AIR_CONTROL_PAYLOAD, 'previous_state': None},
            'Кухня: в режиме по воздуху включил бы обогрев (20,9° при цели 22,0°)',
        ),
        (
            {**AIR_CONTROL_PAYLOAD, 'air': '21.8', 'mode': 'comfort', 'state': 'idle', 'setpoint': '30.0'},
            'Кухня: в режиме по воздуху держал бы термостат на 30°',
        ),
        (
            {**AIR_CONTROL_PAYLOAD, 'previous_state': 'heat', 'power': False, 'setpoint': None},
            'Кухня: в режиме по воздуху выключил бы термостат',
        ),
        ({**AIR_CONTROL_PAYLOAD, 'previous_state': 'heat', 'setpoint': None}, 'Кухня: неизвестное событие'),
        ({}, 'Кухня: неизвестное событие'),
    ],
    ids=[
        'heat',
        'idle',
        'first_after_start',
        'thermostat_set_beside_kept_state',
        'thermostat_off_beside_kept_state',
        'kept_state_without_setpoint',
        'empty',
    ],
)
def test_air_control_text_says_what_air_mode_would_do(payload: dict[str, object], expected: str) -> None:
    assert air_control_text(_entry('air_control_dry_run', payload)) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        (AIR_CONTROL_PAYLOAD, 'На термостате было бы 30°'),
        ({**AIR_CONTROL_PAYLOAD, 'power': False, 'setpoint': None}, 'Термостат был бы выключен'),
        ({**AIR_CONTROL_PAYLOAD, 'state': 'idle', 'setpoint': '30.0'}, ''),
        ({}, ''),
    ],
    ids=['setpoint', 'off', 'kept_state_says_it_in_the_headline', 'empty'],
)
def test_air_control_note_gives_the_would_be_thermostat_setting(payload: dict[str, object], expected: str) -> None:
    assert air_control_note(_entry('air_control_dry_run', payload)) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        (
            {'lead_minutes': 140, 'start': '2026-10-29T01:10:00Z', 'fixed_start': '2026-10-29T00:30:00Z'},
            'Кухня: в режиме по воздуху начал бы разогрев в 04:10, а не в 03:30',
        ),
        (
            {'lead_minutes': 180, 'start': '2026-10-29T00:30:00Z', 'fixed_start': '2026-10-29T00:30:00Z'},
            'Кухня: в режиме по воздуху тоже начал бы разогрев в 03:30',
        ),
        (
            {'lead_minutes': 140, 'start': 'soon', 'fixed_start': '2026-10-29T00:30:00Z'},
            'Кухня: неизвестное событие',
        ),
        ({}, 'Кухня: неизвестное событие'),
    ],
    ids=['later', 'same', 'malformed', 'empty'],
)
def test_optimal_start_text_compares_the_would_be_start_with_the_fixed_one(
    payload: dict[str, object],
    expected: str,
) -> None:
    assert optimal_start_text(_entry('optimal_start_dry_run', payload)) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        (
            {'active': True, 'air': '18.8', 'setpoint': '30.0'},
            'Санузел 2 этаж: защита второго этажа включила бы комфорт (2 этаж 18,8°)',
        ),
        (
            {'active': False, 'air': '19.3', 'setpoint': '30.0'},
            'Санузел 2 этаж: защита второго этажа вернула бы расписание (2 этаж 19,3°)',
        ),
        (
            {'active': False, 'air': None, 'setpoint': '30.0'},
            'Санузел 2 этаж: защита второго этажа вернула бы расписание (нет показаний 2 этажа)',
        ),
        ({}, 'Санузел 2 этаж: неизвестное событие'),
    ],
    ids=['on', 'off', 'off_without_a_reading', 'empty'],
)
def test_second_floor_guard_text_says_what_the_guard_would_do(payload: dict[str, object], expected: str) -> None:
    assert second_floor_guard_text(_entry('second_floor_guard_dry_run', payload, name='Санузел 2 этаж')) == expected


@pytest.mark.usefixtures('english')
def test_dry_run_texts_follow_the_active_language() -> None:
    entry = _entry('air_control_dry_run', AIR_CONTROL_PAYLOAD, name='Kitchen')

    assert (air_control_text(entry), air_control_note(entry)) == (
        'Kitchen: air mode would start heating (20.9° against a target of 22.0°)',
        'Thermostat would be set to 30°',
    )


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


@freeze_time('2026-10-31T18:00:00Z')
@pytest.mark.usefixtures('russian')
def test_outage_question_names_no_date_for_an_outage_today() -> None:
    assert outage_question(OUTAGE) == 'Свет пропадал 20:24–20:33 (9 мин). Что это было?'


@freeze_time('2026-11-01T06:00:00Z')
@pytest.mark.usefixtures('russian')
def test_outage_question_names_the_date_of_an_outage_on_another_day() -> None:
    assert outage_question(OUTAGE) == 'Свет пропадал 31 октября 20:24–20:33 (9 мин). Что это было?'


@freeze_time('2026-10-31T18:00:00Z')
@pytest.mark.usefixtures('english')
def test_outage_question_follows_the_active_language() -> None:
    assert outage_question(OUTAGE) == 'Power was out 20:24–20:33 (9 min). What was it?'


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'created_at', 'expected'),
    [
        (
            {'outage_id': 7, 'began_at': '2026-10-31T17:24:40Z', 'ended_at': '2026-10-31T17:33:05Z'},
            datetime(2026, 10, 31, 20, 40, tzinfo=MOSCOW),
            'Свет пропадал 20:24–20:33 (9 мин)',
        ),
        (
            {'outage_id': 7, 'began_at': '2026-10-31T20:50:00Z', 'ended_at': '2026-10-31T21:20:00Z'},
            datetime(2026, 11, 1, 0, 30, tzinfo=MOSCOW),
            'Свет пропадал 31 октября 23:50–00:20 (30 мин)',
        ),
        ({}, datetime(2026, 10, 31, 20, 40, tzinfo=MOSCOW), 'Неизвестное событие'),
        (
            {'outage_id': 7, 'began_at': 'yesterday', 'ended_at': '2026-10-31T17:33:05Z'},
            datetime(2026, 10, 31, 20, 40, tzinfo=MOSCOW),
            'Неизвестное событие',
        ),
        (
            {'outage_id': 7, 'began_at': '2026-10-31T17:24:40', 'ended_at': '2026-10-31T17:33:05'},
            datetime(2026, 10, 31, 20, 40, tzinfo=MOSCOW),
            'Неизвестное событие',
        ),
    ],
    ids=['same_day', 'across_midnight', 'empty', 'malformed', 'without_a_time_zone'],
)
def test_power_outage_text_reads_the_journaled_interval(
    payload: dict[str, object],
    created_at: datetime,
    expected: str,
) -> None:
    entry = TimelineEntry(
        kind='power_outage',
        subject='',
        subject_name='',
        device_kind=None,
        payload=payload,
        created_at=created_at,
    )

    assert power_outage_text(entry) == expected
