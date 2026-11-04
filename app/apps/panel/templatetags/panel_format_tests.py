from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.utils import translation
from freezegun import freeze_time

from apps.climate.domain.drive import PendingKind, PendingWrite
from apps.climate.domain.modes import ControlPath, ScheduleMode, ZoneKind
from apps.climate.domain.room_intent import IntentReason, RoomIntent
from apps.climate.domain.week_plan import DayPeriod
from apps.climate.domain.zone_intent import ForcedTarget
from apps.core.domain.errors import DomainError, SecretMissingError
from apps.devices.domain.errors import (
    BrokerUnavailableError,
    DeviceConfigError,
    DeviceNotFoundError,
    DeviceProtocolError,
    DeviceUnavailableError,
    DriverNotImplementedError,
    PlugNotSwitchableError,
    SensorReadingError,
)
from apps.devices.domain.kinds import DeviceKind
from apps.panel.templatetags.panel_format import (
    air_control_note,
    air_control_text,
    alert_text,
    backup_heat_text,
    clock_note,
    command_note,
    command_summary,
    day_minute,
    day_word,
    duration,
    estimate_parts_text,
    external_heat_note,
    external_heat_text,
    failure_note,
    fireplace_mark_text,
    guest_nights,
    guests_text,
    icon,
    learning_reset_note,
    learning_reset_text,
    load_amps,
    load_command_text,
    load_name,
    load_phase,
    load_phase_learned_text,
    load_shed_note,
    load_shed_text,
    local_time,
    manual_change_text,
    morning_rejected_text,
    next_write_text,
    optimal_start_text,
    outage_overload_note,
    outage_question,
    owner_command_text,
    period_text,
    phase_event_text,
    power_outage_text,
    room_override_text,
    second_floor_guard_text,
    settings_change_text,
    start_wait_note,
    start_wait_text,
    steps_down,
    steps_up,
    weekday_name,
    weekday_short,
)
from apps.panel.usecases.get_journal_timeline import TimelineEntry
from apps.panel.usecases.get_panel_overview import LoadOverview, ZoneOverview, ZoneRegime
from apps.panel.usecases.get_phase_currents import EstimatePartOverview, PhaseCurrent
from apps.power.domain.load_state import LoadState
from apps.power.domain.loads import AmpsOrigin, LoadKind, LoadPhase, PhaseOrigin
from apps.power.domain.outage import OutageCause, OutageRecord, PrecedingOverload
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
BOILER_SHED: dict[str, object] = {
    'load': 'boiler-1f',
    'name': 'Бойлер 1 этаж',
    'kind': 'plug',
    'phase': 'l2',
    'amps': '26.4',
    'load_amps': '6.0',
    'reason': 'overload',
}
BATHROOM_SHED_BY_THE_ESTIMATE: dict[str, object] = {
    'load': 'bathroom-1f',
    'name': 'Санузел 1 этаж',
    'kind': 'zone',
    'phase': 'l2',
    'amps': '24.6',
    'load_amps': '6.0',
    'reason': 'overload',
    'estimated': True,
}
L2_ESTIMATE = PhaseCurrent(
    phase='l2',
    amps=Decimal('14.42'),
    stale=False,
    age_seconds=1,
    estimated=True,
    estimate_parts=(
        EstimatePartOverview(name='base', amps=Decimal('2.0')),
        EstimatePartOverview(name='Бойлер 2 этаж', amps=Decimal('6.42')),
        EstimatePartOverview(name='Санузел 1 этаж', amps=Decimal('6.0')),
    ),
)
KITCHEN_SHED: dict[str, object] = {
    'load': 'kitchen',
    'name': 'Кухня',
    'kind': 'zone',
    'phase': 'unknown',
    'amps': None,
    'load_amps': '6.0',
    'reason': 'meter_lost',
}
BOILER_LOAD = LoadOverview(
    slug='boiler-1f',
    name='Бойлер 1 этаж',
    kind=LoadKind.PLUG,
    zone=None,
    device='plug-boiler-1f',
    priority=1,
    phase=LoadPhase.L2,
    phase_origin=PhaseOrigin.LEARNED,
    learned_phase=LoadPhase.L2,
    learned_amps=Decimal('6.6'),
    learning_samples=3,
    nominal_amps=Decimal('6.0'),
    amps=Decimal('6.0'),
    amps_origin=AmpsOrigin.NOMINAL,
    state=LoadState.DRAWING,
    live=False,
)


PANEL_NOW = datetime(2026, 11, 4, 16, 40, tzinfo=MOSCOW)
HOLD_ENDS = PANEL_NOW + timedelta(hours=2)
KITCHEN_CHANGE: dict[str, object] = {
    'zone': 'kitchen',
    'name': 'Кухня',
    'power': None,
    'mode': None,
    'setpoint': ['28.0', '25.0'],
    'until': '2026-11-04T15:40:00Z',
    'dry_run': False,
}
KITCHEN = ZoneOverview(
    slug='kitchen',
    name='Кухня',
    kind=ZoneKind.FLOOR,
    mode=ScheduleMode.COMFORT,
    available=True,
    error_code=None,
    error=None,
    floor_temperature=Decimal('24.5'),
    power=True,
    setpoint=Decimal('28.0'),
    target_setpoint=Decimal('30.0'),
    boosted=False,
    live_control=True,
    air_control_live=False,
    path=ControlPath.FLOOR,
    air_target=None,
    regime=ZoneRegime.COMFORT,
    lead=None,
    boost_until=None,
    next_change=None,
    air=None,
)
OPEN_SPACE = {'room': 'open-space', 'name': 'Гостиная, кухня и прихожая'}
FIREPLACE: dict[str, object] = {
    'fireplace': 'fireplace',
    'name': 'Камин',
    'room': 'open-space',
    'room_name': 'Гостиная, кухня и прихожая',
}
OPEN_SPACE_HEAT: dict[str, object] = {'room': 'open-space', 'room_name': 'Гостиная, кухня и прихожая'}


def _intent(target: str) -> RoomIntent:
    return RoomIntent(
        room='open-space',
        reason=IntentReason.OVERRIDE,
        target=Decimal(target),
        until=PANEL_NOW,
        forced=(ForcedTarget(target=Decimal(target), beneath=Decimal('22.0')),),
    )


def _entry(kind: str, payload: dict[str, object], name: str = 'Кухня') -> TimelineEntry:
    return TimelineEntry(
        kind=kind,
        subject='kitchen',
        subject_name=name,
        device_kind=None,
        payload=payload,
        created_at=datetime(2026, 10, 29, 7, 0, tzinfo=MOSCOW),
    )


def _load_entry(
    kind: str,
    payload: dict[str, object],
    subject: str = 'boiler-1f',
    name: str = 'Бойлер 1 этаж',
    device_kind: DeviceKind | None = DeviceKind.PLUG,
) -> TimelineEntry:
    return TimelineEntry(
        kind=kind,
        subject=subject,
        subject_name=name,
        device_kind=device_kind,
        payload=payload,
        created_at=datetime(2026, 10, 31, 20, 24, tzinfo=MOSCOW),
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
        (
            PlugNotSwitchableError('plug-boiler-1f', 'its driver only reads'),
            'Драйвер этой розетки не умеет её переключать',
        ),
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
            'Кухня: наблюдение по воздуху возобновлено, сейчас грел бы (20,9° при цели 22,0°)',
        ),
        (
            {
                **AIR_CONTROL_PAYLOAD,
                'air': '21.8',
                'mode': 'comfort',
                'state': 'idle',
                'previous_state': None,
                'setpoint': '18.0',
            },
            'Кухня: наблюдение по воздуху возобновлено, сейчас не грел бы (21,8° при цели 22,0°)',
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
        'resumed_heating',
        'resumed_idle',
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
        (
            {**AIR_CONTROL_PAYLOAD, 'state': 'idle', 'previous_state': None, 'setpoint': '18.0'},
            'На термостате было бы 18°',
        ),
        ({}, ''),
    ],
    ids=['setpoint', 'off', 'kept_state_says_it_in_the_headline', 'resumed_observation', 'empty'],
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


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        (
            {'room': 'bedroom', 'room_name': 'Спальня', 'zone': 'bathroom-2f', 'active': True, 'air': '18.8'},
            'Санузел 2 этаж: резервный обогрев включил бы комфорт (Спальня 18,8°)',
        ),
        (
            {'room': 'bedroom', 'room_name': 'Спальня', 'zone': 'bathroom-2f', 'active': False, 'air': '19.3'},
            'Санузел 2 этаж: резервный обогрев вернул бы расписание (Спальня 19,3°)',
        ),
        (
            {'room': 'bedroom', 'room_name': 'Спальня', 'zone': 'bathroom-2f', 'active': False, 'air': None},
            'Санузел 2 этаж: резервный обогрев вернул бы расписание (нет показаний: Спальня)',
        ),
        ({'active': True, 'air': '18.8'}, 'Санузел 2 этаж: неизвестное событие'),
        ({}, 'Санузел 2 этаж: неизвестное событие'),
    ],
    ids=['on', 'off', 'off_without_a_reading', 'no_room', 'empty'],
)
def test_backup_heat_text_says_what_backup_heat_would_do_and_names_its_room(
    payload: dict[str, object],
    expected: str,
) -> None:
    assert backup_heat_text(_entry('backup_heat_dry_run', payload, name='Санузел 2 этаж')) == expected


@pytest.mark.usefixtures('english')
def test_dry_run_texts_follow_the_active_language() -> None:
    entry = _entry('air_control_dry_run', AIR_CONTROL_PAYLOAD, name='Kitchen')

    assert (air_control_text(entry), air_control_note(entry)) == (
        'Kitchen: air mode would start heating (20.9° against a target of 22.0°)',
        'Thermostat would be set to 30°',
    )


@pytest.mark.usefixtures('english')
def test_resumed_observation_follows_the_active_language() -> None:
    entry = _entry('air_control_dry_run', {**AIR_CONTROL_PAYLOAD, 'previous_state': None}, name='Kitchen')

    assert air_control_text(entry) == (
        'Kitchen: watching air mode again, it would be heating (20.9° against a target of 22.0°)'
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


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('overload', 'expected'),
    [
        (
            PrecedingOverload(phase=LoadPhase.L2, amps=Decimal('31.27')),
            'Перед отключением фаза L2 была перегружена: 31,3 А',
        ),
        (PrecedingOverload(phase=LoadPhase.L3, amps=None), 'Перед отключением фаза L3 была перегружена'),
        (PrecedingOverload(phase=LoadPhase.UNKNOWN, amps=None), 'Перед отключением была перегрузка фазы'),
        (None, ''),
    ],
    ids=['phase_and_current', 'without_a_current', 'unreadable_phase', 'no_overload'],
)
def test_outage_overload_note_says_which_phase_was_overloaded_before_the_cut(
    overload: PrecedingOverload | None,
    expected: str,
) -> None:
    assert outage_overload_note(replace(OUTAGE, overload=overload)) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('load', 'expected'),
    [
        (BOILER_LOAD, ('Фаза L2, выучена', '6,0 А, номинал')),
        (
            replace(BOILER_LOAD, phase_origin=PhaseOrigin.SET, amps=Decimal('6.6'), amps_origin=AmpsOrigin.LEARNED),
            ('Фаза L2, задана', '6,6 А, выучен'),
        ),
        (
            replace(
                BOILER_LOAD,
                phase=LoadPhase.UNKNOWN,
                phase_origin=PhaseOrigin.UNKNOWN,
                amps_origin=AmpsOrigin.DEFAULT,
            ),
            ('Фаза неизвестна', '6,0 А по умолчанию'),
        ),
    ],
    ids=['learned_phase_nominal_current', 'set_phase_learned_current', 'unknown_phase_default_current'],
)
def test_load_facts_say_where_the_phase_and_the_current_come_from(
    load: LoadOverview,
    expected: tuple[str, str],
) -> None:
    assert (load_phase(load), load_amps(load)) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('kind', 'payload', 'expected'),
    [
        ('phase_overload', {'phase': 'l2', 'amps': '26.4'}, 'Перегрузка фазы L2: 26,4 А'),
        ('phase_calm', {'phase': 'l2', 'amps': '17.2'}, 'Перегрузка фазы L2 прошла: 17,2 А'),
        (
            'ev_charging_started',
            {'phase': 'l3', 'amps': '16.0', 'before': '4.0'},
            'Фаза L3: похоже, заряжается электромобиль, +16,0 А',
        ),
        (
            'ev_charging_stopped',
            {'phase': 'l3', 'amps': '16.0', 'before': '4.0'},
            'Фаза L3: электромобиль больше не заряжается',
        ),
        ('phase_overload', {'phase': 'l4', 'amps': '26.4'}, 'l2: неизвестное событие'),
        ('phase_overload', {'phase': 'l2'}, 'l2: неизвестное событие'),
        (
            'phase_overload',
            {'phase': 'l2', 'amps': '24.6', 'estimated': True},
            'Перегрузка фазы L2 по оценке: 24,6 А',
        ),
        (
            'phase_calm',
            {'phase': 'l2', 'amps': '17.2', 'estimated': True},
            'Перегрузка фазы L2 по оценке прошла: 17,2 А',
        ),
        ('phase_overload', {'phase': 'l2', 'amps': '26.4', 'estimated': False}, 'Перегрузка фазы L2: 26,4 А'),
    ],
    ids=[
        'overload',
        'calm',
        'ev_charging_started',
        'ev_charging_stopped',
        'unknown_phase',
        'without_a_current',
        'overload_by_the_estimate',
        'calm_by_the_estimate',
        'overload_measured',
    ],
)
def test_phase_event_text_names_the_phase_and_its_current(
    kind: str,
    payload: dict[str, object],
    expected: str,
) -> None:
    assert phase_event_text(_load_entry(kind, payload, subject='l2', name='l2', device_kind=None)) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('kind', 'payload', 'expected'),
    [
        ('load_shed', BOILER_SHED, ('Бойлер 1 этаж: выключил розетку', 'Перегрузка фазы L2: 26,4 А')),
        (
            'load_shed_dry_run',
            BOILER_SHED,
            ('Бойлер 1 этаж: выключил бы розетку', 'Перегрузка фазы L2: 26,4 А'),
        ),
        (
            'load_shed_dry_run',
            {**BOILER_SHED, 'amps': None},
            ('Бойлер 1 этаж: выключил бы розетку', 'Перегрузка фазы L2'),
        ),
        (
            'load_shed',
            KITCHEN_SHED,
            ('Кухня: выключил термостат', 'Нет данных от фазометра: по одному полу на фазу'),
        ),
        (
            'load_restored',
            {**BOILER_SHED, 'amps': '17.2', 'cause': 'calm'},
            ('Бойлер 1 этаж: снова включил розетку', 'Фаза L2 в норме: 17,2 А'),
        ),
        (
            'load_restored_dry_run',
            {**KITCHEN_SHED, 'cause': 'calm'},
            ('Кухня: вернул бы термостат расписанию', 'Фазы в норме'),
        ),
        (
            'load_restored',
            {**KITCHEN_SHED, 'cause': 'released', 'switched_on': False},
            ('Кухня: вернул термостат расписанию', 'Управление нагрузкой переключено в настройках'),
        ),
        (
            'load_restored',
            {**KITCHEN_SHED, 'cause': 'released', 'switched_on': True},
            ('Кухня: снова включил термостат', 'Управление нагрузкой переключено в настройках'),
        ),
        (
            'load_restored',
            {**KITCHEN_SHED, 'cause': 'limp', 'switched_on': False},
            ('Кухня: вернул термостат расписанию', 'Нет данных от фазометра: на этой фазе не греет другой пол'),
        ),
        ('load_shed', {}, ('Бойлер 1 этаж: выключил розетку', '')),
        (
            'load_shed_dry_run',
            BATHROOM_SHED_BY_THE_ESTIMATE,
            ('Санузел 1 этаж: выключил бы термостат', 'Перегрузка фазы L2 по оценке: 24,6 А'),
        ),
        (
            'load_shed_dry_run',
            {**BATHROOM_SHED_BY_THE_ESTIMATE, 'amps': None},
            ('Санузел 1 этаж: выключил бы термостат', 'Перегрузка фазы L2 по оценке'),
        ),
        (
            'load_restored_dry_run',
            {**BATHROOM_SHED_BY_THE_ESTIMATE, 'amps': '17.2', 'cause': 'calm'},
            ('Санузел 1 этаж: вернул бы термостат расписанию', 'Фаза L2 по оценке в норме: 17,2 А'),
        ),
    ],
    ids=[
        'plug_shed',
        'plug_shed_in_dry_run',
        'plug_shed_without_a_current',
        'zone_shed_on_a_lost_meter',
        'plug_restored_on_calm',
        'zone_restored_in_dry_run_on_calm_of_every_phase',
        'zone_released_to_its_schedule',
        'zone_released_with_its_thermostat_switched_back_on',
        'zone_restored_while_the_meter_is_lost',
        'kind_from_the_device',
        'zone_shed_by_the_estimate',
        'zone_shed_by_the_estimate_without_a_current',
        'zone_restored_on_calm_by_the_estimate',
    ],
)
def test_load_shed_text_says_what_was_switched_and_why(
    kind: str,
    payload: dict[str, object],
    expected: tuple[str, str],
) -> None:
    entry = _load_entry(
        kind,
        payload,
        subject=str(payload.get('load', 'boiler-1f')),
        name=str(payload.get('name', 'Бойлер 1 этаж')),
    )

    assert (load_shed_text(entry), load_shed_note(entry)) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('command', 'name', 'device_kind', 'expected'),
    [
        ('shed', 'Бойлер 1 этаж', DeviceKind.PLUG, 'Бойлер 1 этаж: не удалось выключить розетку'),
        ('restore', 'Бойлер 1 этаж', DeviceKind.PLUG, 'Бойлер 1 этаж: не удалось снова включить розетку'),
        ('shed', 'Кухня', DeviceKind.THERMOSTAT, 'Кухня: не удалось выключить термостат'),
        ('restore', 'Кухня', DeviceKind.THERMOSTAT, 'Кухня: не удалось снова включить термостат'),
        ('shed', 'Бойлер 1 этаж', None, 'Бойлер 1 этаж: команда управления нагрузкой не прошла'),
    ],
    ids=['plug_off', 'plug_on', 'thermostat_off', 'thermostat_on', 'load_no_longer_known'],
)
def test_load_command_text_names_the_device_teplo_could_not_switch(
    command: str,
    name: str,
    device_kind: DeviceKind | None,
    expected: str,
) -> None:
    entry = _load_entry('load_command_failed', {'command': command}, name=name, device_kind=device_kind)

    assert load_command_text(entry) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        ({'phase': 'l2', 'amps': '21.5', 'reason': 'no_headroom'}, 'Фаза L2 дошла бы до 21,5 А'),
        ({'phase': 'l2', 'amps': None, 'reason': 'too_soon'}, 'Другая нагрузка только что включилась'),
        ({'phase': 'l2', 'amps': None, 'reason': 'meter_stale'}, 'Нет свежих данных от фазометра'),
        (
            {'phase': 'unknown', 'amps': None, 'reason': 'phase_taken'},
            'Фазометр молчит, а на этой фазе уже греет другой пол',
        ),
        (
            {'phase': 'l2', 'amps': None, 'reason': 'phase_overloaded'},
            'Фазометр молчит, а фаза замолчала перегруженной',
        ),
        (
            {'phase': 'l2', 'amps': None, 'reason': 'phase_not_calm'},
            'Фазометр молчит, а фаза замолчала выше порога возврата',
        ),
        (
            {'phase': 'l2', 'amps': '24.5', 'reason': 'phase_would_overload'},
            'Фазометр молчит, а фаза L2 по последнему показанию дошла бы до 24,5 А',
        ),
        ({}, ''),
        (
            {'phase': 'l2', 'amps': '21.5', 'reason': 'no_headroom', 'estimated': True},
            'Фаза L2 по оценке дошла бы до 21,5 А',
        ),
        (
            {'phase': 'l2', 'amps': '24.5', 'reason': 'phase_would_overload', 'estimated': True},
            'Фазометр молчит, а фаза L2 по последней оценке дошла бы до 24,5 А',
        ),
        (
            {'phase': 'l2', 'amps': None, 'reason': 'too_soon', 'estimated': True},
            'Другая нагрузка только что включилась',
        ),
    ],
    ids=[
        'no_headroom',
        'too_soon',
        'meter_stale',
        'phase_taken',
        'phase_overloaded',
        'phase_not_calm',
        'phase_would_overload',
        'empty',
        'no_headroom_by_the_estimate',
        'phase_would_overload_by_the_last_estimate',
        'too_soon_on_the_estimated_phase',
    ],
)
def test_start_wait_says_why_a_start_would_wait(payload: dict[str, object], expected: str) -> None:
    entry = _load_entry('start_would_wait_dry_run', payload, subject='kitchen', name='Кухня')

    assert (start_wait_text(entry), start_wait_note(entry)) == ('Кухня: отложил бы включение', expected)


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        ({'phase': 'l2', 'amps': '6.2', 'samples': 3}, 'Бойлер 1 этаж: выучил фазу L2, 6,2 А'),
        ({'phase': 'unknown', 'amps': '6.2', 'samples': 3}, 'Бойлер 1 этаж: неизвестное событие'),
    ],
    ids=['learned', 'malformed'],
)
def test_load_phase_learned_text_names_the_phase_and_the_current(payload: dict[str, object], expected: str) -> None:
    assert load_phase_learned_text(_load_entry('load_phase_learned', payload)) == expected


@pytest.mark.parametrize(
    ('name', 'payload', 'expected'),
    [
        ('Бойлер 1 этаж', {'name': 'Бойлер'}, 'Бойлер 1 этаж'),
        ('boiler-1f', {'name': 'Бойлер'}, 'Бойлер'),
        ('boiler-1f', {}, 'boiler-1f'),
    ],
    ids=['current_name', 'name_journaled_for_a_load_no_longer_known', 'slug'],
)
def test_load_name_falls_back_to_the_journaled_name(name: str, payload: dict[str, object], expected: str) -> None:
    assert load_name(_load_entry('load_shed', payload, name=name)) == expected


@pytest.mark.usefixtures('russian')
def test_estimate_parts_text_lists_the_base_and_each_load_with_its_current() -> None:
    assert estimate_parts_text(L2_ESTIMATE) == 'база 2,0 А · Бойлер 2 этаж 6,4 А · Санузел 1 этаж 6,0 А'


@pytest.mark.usefixtures('english')
def test_estimate_texts_follow_the_active_language() -> None:
    entry = _load_entry('phase_overload', {'phase': 'l2', 'amps': '24.6', 'estimated': True}, subject='l2', name='l2')

    assert (phase_event_text(entry), estimate_parts_text(L2_ESTIMATE)) == (
        'Overload on phase L2 by the estimate: 24.6 A',
        'base 2.0 A · Бойлер 2 этаж 6.4 A · Санузел 1 этаж 6.0 A',
    )


@pytest.mark.usefixtures('english')
def test_load_texts_follow_the_active_language() -> None:
    entry = _load_entry('load_shed_dry_run', BOILER_SHED, name='Water heater 1F')

    assert (load_shed_text(entry), load_shed_note(entry), load_phase(BOILER_LOAD)) == (
        'Water heater 1F: would switch the plug off',
        'Overload on phase L2: 26.4 A',
        'Phase L2, learned',
    )


def _settings_entry(subject: str, name: str, payload: dict[str, object]) -> TimelineEntry:
    return TimelineEntry(
        kind='settings_changed',
        subject=subject,
        subject_name=name,
        device_kind=None,
        payload=payload,
        created_at=datetime(2026, 11, 3, 12, 0, tzinfo=MOSCOW),
    )


@pytest.mark.parametrize(('minutes', 'expected'), [(0, '00:00'), (390, '06:30'), (1425, '23:45'), (1440, '24:00')])
def test_day_minute_reads_minutes_after_midnight_as_a_clock(minutes: int, expected: str) -> None:
    assert day_minute(minutes) == expected


@pytest.mark.usefixtures('russian')
def test_period_text_names_the_mode_and_its_hours() -> None:
    assert [
        period_text(DayPeriod(mode=ScheduleMode.PREHEAT, start=210, end=390)),
        period_text(DayPeriod(mode=ScheduleMode.COMFORT, start=390, end=1410)),
        period_text(DayPeriod(mode=ScheduleMode.NIGHT, start=1410, end=1440)),
    ] == ['Разогрев 03:30–06:30', 'Комфорт 06:30–23:30', 'Ночь 23:30–24:00']


@pytest.mark.usefixtures('russian')
def test_weekday_names_follow_the_language() -> None:
    assert ([weekday_name(0), weekday_name(6)], [weekday_short(0), weekday_short(6)]) == (
        ['Понедельник', 'Воскресенье'],
        ['Пн', 'Вс'],
    )


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('entry', 'expected'),
    [
        (
            _settings_entry(
                'kitchen',
                'Кухня',
                {'object': 'zone', 'name': 'Кухня', 'field': 'comfort_floor_setpoint', 'old': '28.0', 'new': '27.5'},
            ),
            'Кухня: пол в комфорте 28° → 27,5°',
        ),
        (
            _settings_entry(
                'kitchen',
                'Кухня',
                {'object': 'zone', 'name': 'Кухня', 'field': 'preheat_lead_minutes', 'old': 180, 'new': 150},
            ),
            'Кухня: разогрев заранее 3 ч → 2 ч 30 мин',
        ),
        (
            _settings_entry(
                'kitchen',
                'Кухня',
                {'object': 'zone', 'name': 'Кухня', 'field': 'heating_enabled', 'old': True, 'new': False},
            ),
            'Кухня: отопление выключено',
        ),
        (
            _settings_entry(
                'kitchen',
                'Кухня-столовая',
                {'object': 'zone', 'name': 'Кухня-столовая', 'field': 'name', 'old': 'Кухня', 'new': 'Кухня-столовая'},
            ),
            'Кухня-столовая: новое название, было «Кухня»',
        ),
        (
            _settings_entry(
                'kitchen',
                'Кухня',
                {
                    'object': 'schedule',
                    'name': 'Кухня',
                    'field': 'monday',
                    'old': ['06:30-23:30'],
                    'new': ['07:00-12:00', '14:00-23:30'],
                },
            ),
            'Кухня, понедельник: комфорт 06:30–23:30 → 07:00–12:00, 14:00–23:30',
        ),
        (
            _settings_entry(
                'kitchen',
                'Кухня',
                {'object': 'schedule', 'name': 'Кухня', 'field': 'sunday', 'old': [], 'new': ['09:00-22:00']},
            ),
            'Кухня, воскресенье: комфорт нет → 09:00–22:00',
        ),
        (
            _settings_entry(
                '',
                '',
                {'object': 'climate_settings', 'name': '', 'field': 'air_start_offset', 'old': '0.30', 'new': '0.25'},
            ),
            'Климат: греть ниже цели на 0,30° → 0,25°',
        ),
        (
            _settings_entry(
                '',
                '',
                {'object': 'climate_settings', 'name': '', 'field': 'air_min_band', 'old': '0.20', 'new': '0.30'},
            ),
            'Климат: минимальный зазор между включением и остановкой 0,20° → 0,30°',
        ),
        (
            _settings_entry(
                '',
                '',
                {'object': 'load_settings', 'name': '', 'field': 'shed_hold_seconds', 'old': 3, 'new': 5},
            ),
            'Управление нагрузкой: отключать через 3 с → 5 с',
        ),
        (
            _settings_entry(
                'boiler-1f',
                'Бойлер 1 этаж',
                {'object': 'load', 'name': 'Бойлер 1 этаж', 'field': 'nominal_amps', 'old': '6.0', 'new': None},
            ),
            'Бойлер 1 этаж: номинальный ток 6 А → не задан',
        ),
        (
            _settings_entry(
                'boiler-1f',
                'Бойлер 1 этаж',
                {'object': 'load', 'name': 'Бойлер 1 этаж', 'field': 'priority', 'old': 1, 'new': 2},
            ),
            'Бойлер 1 этаж: приоритет 1 → 2',
        ),
        (
            _settings_entry(
                'attic',
                'attic',
                {'object': 'zone', 'name': 'Чердак', 'field': 'colour', 'old': 'red', 'new': 'blue'},
            ),
            'Чердак: настройки изменены',
        ),
        (
            _settings_entry(
                'bedroom',
                'bedroom',
                {'object': 'room', 'name': 'Спальня', 'field': 'comfort_target', 'old': '20.5', 'new': '21.0'},
            ),
            'Спальня: воздух в комфорте 20,5° → 21°',
        ),
        (
            _settings_entry(
                'bathroom-1f',
                'Санузел 1 этаж: пол',
                {'object': 'room', 'name': 'Санузел 1 этаж', 'field': 'setback_target', 'old': '19.5', 'new': '19.0'},
            ),
            'Санузел 1 этаж: воздух ночью 19,5° → 19°',
        ),
        (
            _settings_entry(
                'bedroom',
                'bedroom',
                {'object': 'room', 'name': 'Спальня', 'field': 'backup_below', 'old': '19.0', 'new': '18.5'},
            ),
            'Спальня: резервный обогрев ниже 19° → 18,5°',
        ),
        (
            _settings_entry(
                'bedroom',
                'bedroom',
                {'object': 'room', 'name': 'Спальня', 'field': 'backup_hysteresis', 'old': '0.30', 'new': '0.50'},
            ),
            'Спальня: гистерезис резервного обогрева 0,30° → 0,50°',
        ),
        (
            _settings_entry(
                '',
                '',
                {
                    'object': 'climate_settings',
                    'name': '',
                    'field': 'second_floor_guard_threshold',
                    'old': '19.0',
                    'new': '18.5',
                },
            ),
            'Климат: защита второго этажа ниже 19° → 18,5°',
        ),
        (
            _settings_entry(
                'open-space',
                'open-space',
                {
                    'object': 'room',
                    'name': 'Гостиная, кухня и прихожая',
                    'field': 'guest_room',
                    'old': False,
                    'new': True,
                },
            ),
            'Гостиная, кухня и прихожая: теперь гостевая комната',
        ),
        (
            _settings_entry(
                'bathroom-1f',
                'Санузел 1 этаж: пол',
                {'object': 'room', 'name': 'Санузел 1 этаж', 'field': 'guest_room', 'old': True, 'new': False},
            ),
            'Санузел 1 этаж: больше не гостевая комната',
        ),
        (
            _settings_entry(
                '',
                '',
                {'object': 'climate_settings', 'name': '', 'field': 'guests_end_hour', 'old': 14, 'new': 9},
            ),
            'Климат: гости до 14:00 → 09:00',
        ),
    ],
    ids=[
        'setpoint',
        'lead',
        'heating_off',
        'rename',
        'schedule_day',
        'day_given_comfort',
        'climate',
        'climate_band',
        'load_management',
        'nominal_current_cleared',
        'priority',
        'unknown_field_of_a_zone_gone',
        'room_comfort_target',
        'room_named_by_its_payload_not_by_a_zone_of_the_same_slug',
        'backup_heat_threshold',
        'backup_heat_hysteresis',
        'old_guard_entry',
        'made_a_guest_room',
        'no_longer_a_guest_room',
        'guests_end_hour',
    ],
)
def test_settings_change_text_words_what_changed(entry: TimelineEntry, expected: str) -> None:
    assert settings_change_text(entry) == expected


@freeze_time(PANEL_NOW)
@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('zone', 'expected'),
    [
        (
            replace(KITCHEN, pending=PendingWrite(kind=PendingKind.NEXT_WRITE, at=PANEL_NOW + timedelta(minutes=12))),
            'На термостате 28°, 30° — не раньше 16:52',
        ),
        (
            replace(KITCHEN, pending=PendingWrite(kind=PendingKind.NEXT_WRITE, at=PANEL_NOW + timedelta(hours=8))),
            'На термостате 28°, 30° — не раньше 00:40 завтра',
        ),
        (
            replace(KITCHEN, pending=PendingWrite(kind=PendingKind.NEXT_WRITE, at=PANEL_NOW)),
            'На термостате 28°, 30° — в ближайшую минуту',
        ),
        (replace(KITCHEN, pending=PendingWrite(kind=PendingKind.WATCHING, at=None)), ''),
        (replace(KITCHEN, pending=PendingWrite(kind=PendingKind.HOLDS, at=None)), ''),
        (KITCHEN, ''),
        (
            replace(KITCHEN, setpoint=Decimal('25.0'), pending=PendingWrite(kind=PendingKind.HANDS_OFF, at=HOLD_ENDS)),
            'На термостате 25°: teplo вернёт расписание после 18:40',
        ),
        (
            replace(KITCHEN, power=False, pending=PendingWrite(kind=PendingKind.HANDS_OFF, at=HOLD_ENDS)),
            'Термостат выключен: teplo вернёт расписание после 18:40',
        ),
    ],
    ids=[
        'later_today',
        'after_midnight',
        'now',
        'watching',
        'holds',
        'unknown',
        'changed_on_the_thermostat',
        'switched_off_on_the_thermostat',
    ],
)
def test_next_write_text_names_the_setpoint_and_the_earliest_write(zone: ZoneOverview, expected: str) -> None:
    assert next_write_text(zone) == expected


@pytest.mark.parametrize(
    ('target', 'down', 'up'),
    [('22.0', True, True), ('10.5', True, True), ('10.4', False, True), ('10.0', False, True), ('30.0', True, False)],
)
def test_steps_stay_within_the_range_of_an_override(target: str, down: bool, up: bool) -> None:
    assert (steps_down(_intent(target)), steps_up(_intent(target))) == (down, up)


@pytest.mark.usefixtures('russian')
def test_guest_nights_offer_one_to_three_nights_in_words() -> None:
    assert guest_nights() == ((1, '1 ночь'), (2, '2 ночи'), (3, '3 ночи'))


@freeze_time(PANEL_NOW)
@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('kind', 'payload', 'expected'),
    [
        ('guests_set', {'until': '2026-11-05T11:00:00Z', 'nights': 1, 'source': 'panel'}, 'Гости до 14:00 завтра'),
        ('guests_set', {'until': '2026-11-06T11:00:00Z', 'nights': 2, 'source': 'panel'}, 'Гости до 14:00 6 ноября'),
        ('guests_set', {'nights': 1}, 'Кухня: неизвестное событие'),
        ('guests_cleared', {'source': 'telegram'}, 'Режим гостей выключен'),
    ],
    ids=['until_tomorrow', 'for_two_nights', 'unreadable', 'cleared'],
)
def test_guests_text_says_until_when_guests_stay(kind: str, payload: dict[str, object], expected: str) -> None:
    assert guests_text(_entry(kind, payload)) == expected


@freeze_time(PANEL_NOW)
@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('kind', 'payload', 'expected'),
    [
        (
            'room_overridden',
            {**OPEN_SPACE, 'target': '22.5', 'until': '2026-11-04T15:40:00Z', 'source': 'panel'},
            'Гостиная, кухня и прихожая: вручную 22,5° до 18:40',
        ),
        (
            'room_overridden',
            {**OPEN_SPACE, 'target': '23.0', 'until': '2026-11-04T21:10:00Z', 'source': 'telegram'},
            'Гостиная, кухня и прихожая: вручную 23,0° до 00:10 завтра',
        ),
        ('room_override_cleared', {**OPEN_SPACE, 'source': 'panel'}, 'Гостиная, кухня и прихожая: снова по расписанию'),
        ('room_overridden', {'room': 'open-space', 'target': '22.5'}, 'Гостиная: неизвестное событие'),
    ],
    ids=['manual_target', 'manual_target_past_midnight', 'back_to_the_schedule', 'without_a_name'],
)
def test_room_override_text_names_the_room_by_its_journaled_name(
    kind: str,
    payload: dict[str, object],
    expected: str,
) -> None:
    assert room_override_text(_entry(kind, payload, name='Гостиная')) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('source', 'expected'),
    [('telegram', 'Из Telegram'), ('panel', ''), (None, '')],
    ids=['telegram', 'panel', 'none'],
)
def test_command_note_names_a_command_that_came_from_telegram(source: str | None, expected: str) -> None:
    assert command_note(_entry('guests_set', {'source': source})) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        (
            {'update_id': 42, 'bot': '123456', 'text': 'холодно в гостиной', 'command': 'warmer', 'room': 'open-space'},
            'Telegram: «холодно в гостиной»',
        ),
        ({'update_id': 42, 'bot': '123456', 'command': 'help'}, 'Кухня: неизвестное событие'),
    ],
    ids=['command', 'without_text'],
)
def test_owner_command_text_quotes_what_the_owner_wrote(payload: dict[str, object], expected: str) -> None:
    assert owner_command_text(_entry('owner_command', payload)) == expected


@pytest.mark.usefixtures('english')
def test_owner_command_text_follows_the_active_language() -> None:
    assert owner_command_text(_entry('owner_command', {'text': 'статус'})) == 'Telegram: “статус”'


@pytest.mark.usefixtures('english')
def test_intent_texts_follow_the_active_language() -> None:
    entry = _entry('room_override_cleared', {**OPEN_SPACE, 'source': 'telegram'})

    assert (room_override_text(entry), guests_text(_entry('guests_cleared', {})), command_note(entry)) == (
        'Гостиная, кухня и прихожая: back to the schedule',
        'Guests mode is off',
        'From Telegram',
    )


@freeze_time(PANEL_NOW)
@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('kind', 'payload', 'expected'),
    [
        (
            'fireplace_marked',
            {**FIREPLACE, 'until': '2026-11-04T19:10:00Z', 'source': 'panel'},
            'Камин: топится до 22:10',
        ),
        (
            'fireplace_marked',
            {**FIREPLACE, 'until': '2026-11-04T21:30:00Z', 'source': 'telegram'},
            'Камин: топится до 00:30 завтра',
        ),
        ('fireplace_mark_cleared', {**FIREPLACE, 'source': 'panel'}, 'Камин: погас'),
        ('fireplace_marked', {'fireplace': 'fireplace', 'until': '2026-11-04T19:10:00Z'}, 'Кухня: неизвестное событие'),
    ],
    ids=['lit', 'lit_past_midnight', 'out', 'without_a_name'],
)
def test_fireplace_mark_text_names_the_fireplace_and_the_end_of_its_mark(
    kind: str,
    payload: dict[str, object],
    expected: str,
) -> None:
    assert fireplace_mark_text(_entry(kind, payload)) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('kind', 'payload', 'expected'),
    [
        (
            'external_heat_started',
            {**OPEN_SPACE_HEAT, 'rates': {'air-kitchen': '1.62', 'air-living-room': '2.13'}, 'live': False},
            (
                'Гостиная, кухня и прихожая: похоже, топится камин (+2,1°/ч)',
                'Наблюдение: обучение не останавливается',
            ),
        ),
        (
            'external_heat_started',
            {**OPEN_SPACE_HEAT, 'rates': {'air-office': '1.70'}, 'live': True},
            ('Гостиная, кухня и прихожая: похоже, топится камин (+1,7°/ч)', ''),
        ),
        (
            'external_heat_ended',
            {**OPEN_SPACE_HEAT, 'reason': 'calm', 'live': False},
            ('Гостиная, кухня и прихожая: похоже, камин прогорел', ''),
        ),
        (
            'external_heat_ended',
            {**OPEN_SPACE_HEAT, 'reason': 'timeout', 'live': True},
            ('Гостиная, кухня и прихожая: похоже, камин прогорел', 'Прошло 6 часов'),
        ),
        (
            'external_heat_started',
            {**OPEN_SPACE_HEAT, 'rates': {}, 'live': True},
            ('Кухня: неизвестное событие', ''),
        ),
    ],
    ids=['started_while_watching', 'started_live', 'ended_calm', 'ended_after_six_hours', 'without_rates'],
)
def test_external_heat_text_names_the_room_and_the_fastest_rise(
    kind: str,
    payload: dict[str, object],
    expected: tuple[str, str],
) -> None:
    entry = _entry(kind, payload)

    assert (external_heat_text(entry), external_heat_note(entry)) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        (
            {'zone': 'living-room', 'name': 'Гостиная', 'rate': '3.412', 'cap': '1.5', 'outdoor_bucket': -5},
            'Гостиная: утро не выучено: 3,4°/ч быстрее предела 1,5°/ч',
        ),
        (
            {'zone': 'living-room', 'rate': '2.912', 'cap': '2.400'},
            'Кухня: утро не выучено: 2,9°/ч быстрее предела 2,4°/ч',
        ),
        ({'zone': 'living-room', 'name': 'Гостиная'}, 'Кухня: неизвестное событие'),
    ],
    ids=['named', 'named_by_the_zone', 'without_rates'],
)
def test_morning_rejected_text_compares_the_rate_with_the_cap(payload: dict[str, object], expected: str) -> None:
    assert morning_rejected_text(_entry('zone_morning_rejected', payload)) == expected


@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        (
            {
                'zone': 'living-room',
                'name': 'Гостиная',
                'early_stop_offset': '0.25',
                'dead_time_minutes': 35,
                'rates': {'-5': '0.290'},
                'mornings': 4,
            },
            ('Гостиная: обучение сброшено', 'Было: утр 4, задержка 35 мин, раннее отключение 0,25°'),
        ),
        (
            {
                'zone': 'living-room',
                'name': 'Гостиная',
                'early_stop_offset': '0.30',
                'dead_time_minutes': None,
                'rates': {},
                'mornings': 0,
            },
            ('Гостиная: обучение сброшено', 'Было: утр 0, раннее отключение 0,30°'),
        ),
        ({'zone': 'living-room'}, ('Кухня: обучение сброшено', '')),
    ],
    ids=['everything_learned', 'only_the_early_stop', 'unreadable'],
)
def test_learning_reset_text_names_the_zone_and_what_it_had_learned(
    payload: dict[str, object],
    expected: tuple[str, str],
) -> None:
    entry = _entry('zone_learning_reset', payload)

    assert (learning_reset_text(entry), learning_reset_note(entry)) == expected


@pytest.mark.usefixtures('english')
def test_fireplace_and_learning_texts_follow_the_active_language() -> None:
    assert (
        fireplace_mark_text(_entry('fireplace_mark_cleared', FIREPLACE)),
        external_heat_text(_entry('external_heat_ended', OPEN_SPACE_HEAT)),
        learning_reset_note(_entry('zone_learning_reset', {'mornings': 2, 'dead_time_minutes': 40})),
    ) == (
        'Камин: out',
        'Гостиная, кухня и прихожая: looks like the fireplace has burnt out',
        'Was: mornings 2, delay 40 min',
    )


@freeze_time(PANEL_NOW)
@pytest.mark.usefixtures('russian')
@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        (KITCHEN_CHANGE, 'Кухня: изменили на термостате: 28° → 25°, teplo не трогает до 18:40'),
        ({**KITCHEN_CHANGE, 'dry_run': True}, 'Кухня: изменили на термостате: 28° → 25°, teplo не трогал бы до 18:40'),
        (
            {**KITCHEN_CHANGE, 'power': [True, False], 'setpoint': None},
            'Кухня: изменили на термостате: выключили, teplo не трогает до 18:40',
        ),
        (
            {**KITCHEN_CHANGE, 'power': [False, True], 'mode': ['hold', 'smart'], 'setpoint': ['22.0', '22.5']},
            'Кухня: изменили на термостате: включили, режим hold → smart, 22° → 22,5°, teplo не трогает до 18:40',
        ),
        (
            {**KITCHEN_CHANGE, 'name': 'Прихожая', 'until': '2026-11-04T21:30:00Z'},
            'Прихожая: изменили на термостате: 28° → 25°, teplo не трогает до 00:30 завтра',
        ),
        ({**KITCHEN_CHANGE, 'setpoint': None}, 'Кухня: неизвестное событие'),
        ({**KITCHEN_CHANGE, 'until': None}, 'Кухня: неизвестное событие'),
    ],
    ids=[
        'setpoint',
        'setpoint_while_watching',
        'switched_off',
        'switched_on_in_another_mode_at_another_setpoint',
        'held_past_midnight',
        'nothing_changed',
        'without_an_end',
    ],
)
def test_manual_change_text_says_what_was_changed_on_the_thermostat_and_until_when_teplo_leaves_it(
    payload: dict[str, object],
    expected: str,
) -> None:
    assert manual_change_text(_entry('zone_manual_change', payload)) == expected


@freeze_time(PANEL_NOW)
@pytest.mark.usefixtures('english')
def test_manual_change_texts_follow_the_active_language() -> None:
    held = replace(KITCHEN, setpoint=Decimal('25.0'), pending=PendingWrite(kind=PendingKind.HANDS_OFF, at=HOLD_ENDS))

    assert (
        manual_change_text(_entry('zone_manual_change', {**KITCHEN_CHANGE, 'power': [True, False], 'dry_run': True})),
        next_write_text(held),
    ) == (
        'Кухня: changed on the thermostat: switched off, 28° → 25°, teplo would leave it alone until 18:40',
        'Thermostat set to 25°: teplo returns to the schedule after 18:40',
    )
