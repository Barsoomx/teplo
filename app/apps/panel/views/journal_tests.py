from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.devices.domain.kinds import DeviceKind
from apps.panel.usecases.get_journal_timeline import (
    GetJournalTimelineInputDTO,
    GetJournalTimelineOutputDTO,
    TimelineDay,
    TimelineEntry,
)
from apps.power.domain.loads import LoadPhase
from apps.power.domain.outage import OutageCause, OutageRecord, PrecedingOverload

pytestmark = pytest.mark.django_db

MORNING = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
RAW_DETAILS = ('timed out after 5 s', 'Network Error: Device Unreachable', 'thermostat did not answer', 'teplo_stopped')
PLUG_DETAIL = 'unexpected plug payload: {}'
BROKER_DETAIL = 'mqtt connection refused: not authorised'
CUT = MORNING - timedelta(minutes=40)
CUT_PAYLOAD = {'began_at': '2026-10-29T05:20:00Z', 'ended_at': '2026-10-29T05:29:00Z'}
NIGHT_CUT_PAYLOAD = {'outage_id': 8, 'began_at': '2026-10-28T21:50:00Z', 'ended_at': '2026-10-28T22:02:00Z'}
ACTION = 'hx-post="/journal/outages/7/cause/"'
COMMAND_DETAIL = 'Network Error: Device Unreachable'
BOILER = {'load': 'boiler-1f', 'name': 'Бойлер 1 этаж', 'kind': 'plug'}
KITCHEN = {'load': 'kitchen', 'name': 'Кухня', 'kind': 'zone'}
GUARD_ENTRIES: tuple[tuple[str, str, DeviceKind | None, dict[str, object]], ...] = (
    ('phase_overload', 'l2', None, {'phase': 'l2', 'amps': '26.4'}),
    (
        'load_shed_dry_run',
        'boiler-1f',
        DeviceKind.PLUG,
        {**BOILER, 'phase': 'l2', 'amps': '26.4', 'load_amps': '6.0', 'reason': 'overload'},
    ),
    (
        'load_shed',
        'kitchen',
        DeviceKind.THERMOSTAT,
        {**KITCHEN, 'phase': 'l2', 'amps': '25.1', 'load_amps': '8.0', 'reason': 'overload'},
    ),
    (
        'alert_raised',
        'l2',
        None,
        {
            'kind': 'phase_overload',
            'subject': 'l2',
            'name': 'L2',
            'device_kind': 'phase_meter',
            'since': '2026-10-29T05:00:00Z',
            'amps': '31.27',
        },
    ),
    (
        'load_command_failed',
        'boiler-1f',
        DeviceKind.PLUG,
        {**BOILER, 'command': 'restore', 'error': COMMAND_DETAIL, 'error_code': 'device_unavailable'},
    ),
    ('load_shed_overridden', 'boiler-1f', DeviceKind.PLUG, {**BOILER, 'phase': 'l2'}),
    ('phase_calm', 'l2', None, {'phase': 'l2', 'amps': '17.2'}),
    (
        'alert_cleared',
        'l2',
        None,
        {
            'kind': 'phase_overload',
            'subject': 'l2',
            'name': 'L2',
            'device_kind': 'phase_meter',
            'since': '2026-10-29T05:00:00Z',
            'amps': None,
        },
    ),
    (
        'load_restored',
        'kitchen',
        DeviceKind.THERMOSTAT,
        {**KITCHEN, 'phase': 'l2', 'amps': '17.2', 'load_amps': '8.0', 'reason': 'overload', 'cause': 'calm'},
    ),
    (
        'load_restored_dry_run',
        'boiler-1f',
        DeviceKind.PLUG,
        {**BOILER, 'phase': 'l2', 'amps': None, 'load_amps': '6.0', 'reason': 'overload', 'cause': 'released'},
    ),
    (
        'start_would_wait_dry_run',
        'kitchen',
        DeviceKind.THERMOSTAT,
        {**KITCHEN, 'phase': 'l2', 'amps': '21.5', 'load_amps': '8.0', 'reason': 'no_headroom'},
    ),
    ('load_phase_learned', 'boiler-1f', DeviceKind.PLUG, {**BOILER, 'phase': 'l2', 'amps': '6.2', 'samples': 3}),
    ('ev_charging_started', 'l3', None, {'phase': 'l3', 'amps': '16.0', 'before': '4.0'}),
    ('ev_charging_stopped', 'l3', None, {'phase': 'l3', 'amps': '16.0', 'before': '4.0'}),
)


@pytest.fixture
def m_timeline_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal.GetJournalTimelineUseCase') as use_case:
        use_case.init.return_value.execute.return_value = GetJournalTimelineOutputDTO(
            days=(
                TimelineDay(
                    day=date(2026, 10, 29),
                    entries=(
                        TimelineEntry(
                            kind='zone_command_issued',
                            subject='kitchen',
                            subject_name='Кухня',
                            device_kind=None,
                            payload={'change': {'power': None, 'mode': None, 'setpoint': '28.0'}, 'dry_run': True},
                            created_at=MORNING,
                        ),
                    ),
                ),
            ),
        )
        yield use_case


@pytest.fixture
def m_failures_timeline_use_case() -> Iterator[MagicMock]:
    change = {'power': None, 'mode': None, 'setpoint': '28.0'}
    with mock.patch('apps.panel.views.journal.GetJournalTimelineUseCase') as use_case:
        use_case.init.return_value.execute.return_value = GetJournalTimelineOutputDTO(
            days=(
                TimelineDay(
                    day=date(2026, 10, 29),
                    entries=(
                        TimelineEntry(
                            kind='device_availability_changed',
                            subject='thermostat-kitchen',
                            subject_name='Кухня',
                            device_kind=DeviceKind.THERMOSTAT,
                            payload={'available': False, 'reason': RAW_DETAILS[0], 'error_code': 'device_unavailable'},
                            created_at=MORNING,
                        ),
                        TimelineEntry(
                            kind='zone_command_failed',
                            subject='hallway',
                            subject_name='Прихожая',
                            device_kind=None,
                            payload={'change': change, 'error': RAW_DETAILS[1], 'error_code': 'device_unavailable'},
                            created_at=MORNING - timedelta(minutes=5),
                        ),
                        TimelineEntry(
                            kind='zone_command_failed',
                            subject='bathroom-1f',
                            subject_name='Санузел 1 этаж',
                            device_kind=None,
                            payload={'change': change, 'error': RAW_DETAILS[2]},
                            created_at=MORNING - timedelta(minutes=10),
                        ),
                        TimelineEntry(
                            kind=RAW_DETAILS[3],
                            subject='',
                            subject_name='',
                            device_kind=None,
                            payload={},
                            created_at=MORNING - timedelta(minutes=15),
                        ),
                    ),
                ),
            ),
        )
        yield use_case


@pytest.fixture
def m_plug_timeline_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal.GetJournalTimelineUseCase') as use_case:
        use_case.init.return_value.execute.return_value = GetJournalTimelineOutputDTO(
            days=(
                TimelineDay(
                    day=date(2026, 10, 29),
                    entries=(
                        TimelineEntry(
                            kind='device_availability_changed',
                            subject='plug-boiler-1f',
                            subject_name='Бойлер 1 этаж',
                            device_kind=DeviceKind.PLUG,
                            payload={'available': True, 'reason': 'reading received', 'error_code': None},
                            created_at=MORNING,
                        ),
                        TimelineEntry(
                            kind='device_availability_changed',
                            subject='plug-boiler-2f',
                            subject_name='Бойлер 2 этаж',
                            device_kind=DeviceKind.PLUG,
                            payload={'available': False, 'reason': PLUG_DETAIL, 'error_code': 'device_protocol_error'},
                            created_at=MORNING - timedelta(minutes=5),
                        ),
                    ),
                ),
            ),
        )
        yield use_case


@pytest.fixture
def m_sensor_timeline_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal.GetJournalTimelineUseCase') as use_case:
        use_case.init.return_value.execute.return_value = GetJournalTimelineOutputDTO(
            days=(
                TimelineDay(
                    day=date(2026, 10, 29),
                    entries=(
                        TimelineEntry(
                            kind='device_availability_changed',
                            subject='air-kitchen',
                            subject_name='Воздух: кухня',
                            device_kind=DeviceKind.TEMPERATURE_SENSOR,
                            payload={'available': True, 'reason': 'reading received', 'error_code': None},
                            created_at=MORNING,
                        ),
                        TimelineEntry(
                            kind='device_availability_changed',
                            subject='phase-meter',
                            subject_name='Фазометр',
                            device_kind=DeviceKind.PHASE_METER,
                            payload={'available': True, 'reason': 'reading received', 'error_code': None},
                            created_at=MORNING - timedelta(minutes=1),
                        ),
                        TimelineEntry(
                            kind='device_availability_changed',
                            subject='outdoor',
                            subject_name='Улица',
                            device_kind=DeviceKind.TEMPERATURE_SENSOR,
                            payload={'available': False, 'reason': BROKER_DETAIL, 'error_code': 'broker_unavailable'},
                            created_at=MORNING - timedelta(minutes=5),
                        ),
                        TimelineEntry(
                            kind='device_availability_changed',
                            subject='phase-meter',
                            subject_name='Фазометр',
                            device_kind=DeviceKind.PHASE_METER,
                            payload={'available': False, 'reason': 'no reading', 'error_code': 'device_unavailable'},
                            created_at=MORNING - timedelta(minutes=10),
                        ),
                    ),
                ),
            ),
        )
        yield use_case


@pytest.fixture
def m_alerts_timeline_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal.GetJournalTimelineUseCase') as use_case:
        use_case.init.return_value.execute.return_value = GetJournalTimelineOutputDTO(
            days=(
                TimelineDay(
                    day=date(2026, 10, 29),
                    entries=(
                        TimelineEntry(
                            kind='alert_cleared',
                            subject='hallway',
                            subject_name='Прихожая',
                            device_kind=None,
                            payload={
                                'kind': 'zone_off',
                                'subject': 'hallway',
                                'name': 'Прихожая',
                                'device_kind': 'thermostat',
                                'since': '2026-10-29T05:00:00Z',
                            },
                            created_at=MORNING,
                        ),
                        TimelineEntry(
                            kind='alert_raised',
                            subject='thermostat-kitchen',
                            subject_name='Кухня',
                            device_kind=DeviceKind.THERMOSTAT,
                            payload={
                                'kind': 'device_lost',
                                'subject': 'thermostat-kitchen',
                                'name': 'Кухня',
                                'device_kind': 'thermostat',
                                'since': '2026-10-29T05:40:00Z',
                            },
                            created_at=MORNING - timedelta(minutes=5),
                        ),
                        TimelineEntry(
                            kind='alert_cleared',
                            subject='plug-boiler-1f',
                            subject_name='Бойлер 1 этаж',
                            device_kind=DeviceKind.PLUG,
                            payload={
                                'kind': 'device_lost',
                                'subject': 'plug-boiler-1f',
                                'name': 'Бойлер 1 этаж',
                                'device_kind': 'plug',
                                'since': '2026-10-29T05:30:00Z',
                            },
                            created_at=MORNING - timedelta(minutes=10),
                        ),
                    ),
                ),
            ),
        )
        yield use_case


@pytest.fixture
def m_dry_run_timeline_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal.GetJournalTimelineUseCase') as use_case:
        use_case.init.return_value.execute.return_value = GetJournalTimelineOutputDTO(
            days=(
                TimelineDay(
                    day=date(2026, 10, 29),
                    entries=(
                        TimelineEntry(
                            kind='air_control_dry_run',
                            subject='kitchen',
                            subject_name='Кухня',
                            device_kind=None,
                            payload={
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
                            },
                            created_at=MORNING,
                        ),
                        TimelineEntry(
                            kind='air_control_dry_run',
                            subject='living-room',
                            subject_name='Гостиная',
                            device_kind=None,
                            payload={
                                'zone': 'living-room',
                                'air': '21.8',
                                'target': '22.0',
                                'mode': 'comfort',
                                'state': 'idle',
                                'previous_state': 'idle',
                                'power': True,
                                'setpoint': '30.0',
                                'early_stop_offset': '0.30',
                                'lead_minutes': None,
                            },
                            created_at=MORNING - timedelta(minutes=2),
                        ),
                        TimelineEntry(
                            kind='air_control_dry_run',
                            subject='hallway',
                            subject_name='Прихожая',
                            device_kind=None,
                            payload={
                                'zone': 'hallway',
                                'air': '19.4',
                                'target': '19.5',
                                'mode': 'night',
                                'state': 'idle',
                                'previous_state': None,
                                'power': True,
                                'setpoint': '18.0',
                                'start_offset': '0.30',
                                'early_stop_offset': '0.10',
                                'lead_minutes': None,
                            },
                            created_at=MORNING - timedelta(minutes=3),
                        ),
                        TimelineEntry(
                            kind='optimal_start_dry_run',
                            subject='kitchen',
                            subject_name='Кухня',
                            device_kind=None,
                            payload={
                                'zone': 'kitchen',
                                'lead_minutes': 140,
                                'start': '2026-10-29T01:10:00Z',
                                'fixed_start': '2026-10-29T00:30:00Z',
                            },
                            created_at=MORNING - timedelta(minutes=5),
                        ),
                        TimelineEntry(
                            kind='second_floor_guard_dry_run',
                            subject='bathroom-2f',
                            subject_name='Санузел 2 этаж',
                            device_kind=None,
                            payload={'zone': 'bathroom-2f', 'active': True, 'air': '18.8', 'setpoint': '30.0'},
                            created_at=MORNING - timedelta(minutes=10),
                        ),
                    ),
                ),
            ),
        )
        yield use_case


@pytest.fixture
def m_outage_timeline_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal.GetJournalTimelineUseCase') as use_case:
        use_case.init.return_value.execute.return_value = GetJournalTimelineOutputDTO(
            days=(
                TimelineDay(
                    day=date(2026, 10, 29),
                    entries=(
                        TimelineEntry(
                            kind='power_outage',
                            subject='',
                            subject_name='',
                            device_kind=None,
                            payload={'outage_id': 7, **CUT_PAYLOAD},
                            created_at=MORNING,
                            outage=OutageRecord(
                                id=7,
                                began_at=CUT,
                                ended_at=CUT + timedelta(minutes=9),
                                cause=OutageCause.OVERLOAD,
                                cause_marked_at=MORNING,
                            ),
                        ),
                        TimelineEntry(
                            kind='power_outage',
                            subject='',
                            subject_name='',
                            device_kind=None,
                            payload=NIGHT_CUT_PAYLOAD,
                            created_at=MORNING - timedelta(minutes=5),
                        ),
                    ),
                ),
            ),
        )
        yield use_case


@pytest.fixture
def m_guard_timeline_use_case() -> Iterator[MagicMock]:
    names = {'l2': 'l2', 'l3': 'l3', 'boiler-1f': 'Бойлер 1 этаж', 'kitchen': 'Кухня'}
    with mock.patch('apps.panel.views.journal.GetJournalTimelineUseCase') as use_case:
        use_case.init.return_value.execute.return_value = GetJournalTimelineOutputDTO(
            days=(
                TimelineDay(
                    day=date(2026, 10, 29),
                    entries=tuple(
                        TimelineEntry(
                            kind=kind,
                            subject=subject,
                            subject_name=names[subject],
                            device_kind=device_kind,
                            payload=payload,
                            created_at=MORNING - timedelta(minutes=minutes),
                        )
                        for minutes, (kind, subject, device_kind, payload) in enumerate(GUARD_ENTRIES)
                    ),
                ),
            ),
        )
        yield use_case


@pytest.fixture
def m_overloaded_outage_timeline_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal.GetJournalTimelineUseCase') as use_case:
        use_case.init.return_value.execute.return_value = GetJournalTimelineOutputDTO(
            days=(
                TimelineDay(
                    day=date(2026, 10, 29),
                    entries=(
                        TimelineEntry(
                            kind='power_outage',
                            subject='',
                            subject_name='',
                            device_kind=None,
                            payload={'outage_id': 7, **CUT_PAYLOAD},
                            created_at=MORNING,
                            outage=OutageRecord(
                                id=7,
                                began_at=CUT,
                                ended_at=CUT + timedelta(minutes=9),
                                cause=OutageCause.UNMARKED,
                                cause_marked_at=None,
                                overload=PrecedingOverload(phase=LoadPhase.L2, amps=Decimal('26.4')),
                            ),
                        ),
                    ),
                ),
            ),
        )
        yield use_case


SETTINGS_ENTRIES: tuple[tuple[str, str, dict[str, object]], ...] = (
    (
        'kitchen',
        'Кухня',
        {'object': 'zone', 'name': 'Кухня', 'field': 'comfort_floor_setpoint', 'old': '28.0', 'new': '27.5'},
    ),
    (
        'kitchen',
        'Кухня',
        {'object': 'schedule', 'name': 'Кухня', 'field': 'saturday', 'old': ['08:00-23:30'], 'new': []},
    ),
    ('', '', {'object': 'load_settings', 'name': '', 'field': 'shed_amps', 'old': '24.0', 'new': '25.0'}),
    (
        'boiler-1f',
        'Бойлер 1 этаж',
        {'object': 'load', 'name': 'Бойлер 1 этаж', 'field': 'phase', 'old': 'unknown', 'new': 'l2'},
    ),
)


@pytest.fixture
def m_settings_timeline_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal.GetJournalTimelineUseCase') as use_case:
        use_case.init.return_value.execute.return_value = GetJournalTimelineOutputDTO(
            days=(
                TimelineDay(
                    day=date(2026, 11, 3),
                    entries=tuple(
                        TimelineEntry(
                            kind='settings_changed',
                            subject=subject,
                            subject_name=name,
                            device_kind=None,
                            payload=payload,
                            created_at=MORNING - timedelta(minutes=minutes),
                        )
                        for minutes, (subject, name, payload) in enumerate(SETTINGS_ENTRIES)
                    ),
                ),
            ),
        )
        yield use_case


def test_journal_describes_entries_in_words(f_client: Client, m_timeline_use_case: MagicMock) -> None:
    response = f_client.get('/journal/')

    content = response.content.decode()
    assert (
        response.status_code,
        'Кухня: set to 28°' in content,
        'Not sent: watching only' in content,
        m_timeline_use_case.init.return_value.execute.call_args.args,
    ) == (200, True, True, (GetJournalTimelineInputDTO(limit=100),))


def test_journal_explains_failures_in_words_and_keeps_raw_details_in_tooltips(
    f_russian_client: Client,
    m_failures_timeline_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/journal/')

    content = response.content.decode()
    assert (
        'Кухня: нет связи с термостатом' in content,
        content.count('Нет связи с термостатом'),
        'Неизвестная ошибка' in content,
        'Неизвестное событие' in content,
        [(content.count(detail), f'title="{detail}"' in content) for detail in RAW_DETAILS],
        m_failures_timeline_use_case.init.call_count,
    ) == (True, 1, True, True, [(1, True)] * len(RAW_DETAILS), 1)


def test_journal_words_plug_events_as_plug_events(
    f_russian_client: Client,
    m_plug_timeline_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/journal/')

    content = response.content.decode()
    assert (
        'Бойлер 1 этаж: розетка на связи' in content,
        'Бойлер 2 этаж: нет связи с розеткой' in content,
        'Розетка прислала неожиданный ответ' in content,
        'термостат' in content,
        (content.count(PLUG_DETAIL), f'title="{PLUG_DETAIL}"' in content),
        m_plug_timeline_use_case.init.call_count,
    ) == (True, True, True, False, (1, True), 1)


def test_journal_words_sensor_events_as_sensor_events(
    f_russian_client: Client,
    m_sensor_timeline_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/journal/')

    content = response.content.decode()
    assert (
        'Воздух: кухня: датчик на связи' in content,
        'Фазометр: на связи' in content,
        'Улица: нет связи с датчиком' in content,
        'teplo не может подключиться к MQTT-брокеру' in content,
        'Фазометр: нет связи' in content,
        'термостат' in content,
        'Неизвестная ошибка' in content,
        (content.count(BROKER_DETAIL), f'title="{BROKER_DETAIL}"' in content),
        m_sensor_timeline_use_case.init.call_count,
    ) == (True, True, True, True, True, False, False, (1, True), 1)


def test_journal_reads_alerts_like_the_telegram_messages(
    f_russian_client: Client,
    m_alerts_timeline_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/journal/')

    content = response.content.decode()
    assert (
        'Прихожая: термостат снова работает по расписанию' in content,
        'Кухня: нет связи с термостатом с 08:40' in content,
        'Бойлер 1 этаж: розетка снова на связи' in content,
        'неизвестное событие' in content,
        m_alerts_timeline_use_case.init.call_count,
    ) == (True, True, True, False, 1)


def test_journal_words_what_air_control_optimal_start_and_the_guard_would_do(
    f_russian_client: Client,
    m_dry_run_timeline_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/journal/')

    content = response.content.decode()
    assert (
        'Кухня: в режиме по воздуху включил бы обогрев (20,9° при цели 22,0°)' in content,
        content.count('На термостате было бы 30°'),
        'Гостиная: в режиме по воздуху держал бы термостат на 30°' in content,
        'Прихожая: наблюдение по воздуху возобновлено, сейчас не грел бы (19,4° при цели 19,5°)' in content,
        'На термостате было бы 18°' in content,
        'Кухня: в режиме по воздуху начал бы разогрев в 04:10, а не в 03:30' in content,
        'Санузел 2 этаж: защита второго этажа включила бы комфорт (2 этаж 18,8°)' in content,
        'неизвестное событие' in content,
        m_dry_run_timeline_use_case.init.call_count,
    ) == (True, 1, True, True, True, True, True, False, 1)


def test_journal_shows_a_power_outage_with_its_cause_and_the_answers_to_change_it(
    f_russian_client: Client,
    m_outage_timeline_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/journal/')

    content = response.content.decode()
    assert (
        'Свет пропадал 08:20–08:29 (9 мин)' in content,
        'Причина: перегруз, выбило наш автомат' in content,
        content.count(ACTION),
        'value="overload" hx-post="/journal/outages/7/cause/" aria-pressed="true"' in content,
        'hx-target="closest .outage-cause" hx-swap="outerHTML"' in content,
        'Свет пропадал 00:50–01:02 (12 мин)' in content,
        '/journal/outages/8/cause/' in content,
        m_outage_timeline_use_case.init.call_count,
    ) == (True, True, 3, True, True, True, False, 1)


def test_journal_words_what_load_management_saw_and_did(
    f_russian_client: Client,
    m_guard_timeline_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/journal/')

    content = response.content.decode()
    assert (
        [
            text in content
            for text in (
                'Перегрузка фазы L2: 26,4 А',
                'Бойлер 1 этаж: выключил бы розетку',
                'Кухня: выключил термостат',
                'Перегрузка фазы L2: 25,1 А',
                'Фаза L2 перегружена: 31,3 А',
                'Бойлер 1 этаж: не удалось снова включить розетку',
                'Нет связи с розеткой',
                'Бойлер 1 этаж: розетку включили вручную, отключение снято',
                'Перегрузка фазы L2 прошла: 17,2 А',
                'Фаза L2 снова в норме',
                'Кухня: вернул термостат расписанию',
                'Фаза L2 в норме: 17,2 А',
                'Бойлер 1 этаж: снова включил бы розетку',
                'Управление нагрузкой переключено в настройках',
                'Кухня: отложил бы включение',
                'Фаза L2 дошла бы до 21,5 А',
                'Бойлер 1 этаж: выучил фазу L2, 6,2 А',
                'Фаза L3: похоже, заряжается электромобиль, +16,0 А',
                'Фаза L3: электромобиль больше не заряжается',
            )
        ],
        content.count('Перегрузка фазы L2: 26,4 А'),
        (content.count(COMMAND_DETAIL), f'title="{COMMAND_DETAIL}"' in content),
        'неизвестное событие' in content.lower(),
        m_guard_timeline_use_case.init.call_count,
    ) == ([True] * 19, 2, (1, True), False, 1)


def test_journal_says_an_overload_preceded_a_power_outage_and_puts_its_answer_forward(
    f_russian_client: Client,
    m_overloaded_outage_timeline_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/journal/')

    content = response.content.decode()
    assert (
        'Перед отключением фаза L2 была перегружена: 26,4 А' in content,
        'Причина не отмечена' in content,
        content.count('class="answer answer-suggested"'),
        'aria-describedby="outage-7-overload"' in content,
        'id="outage-7-overload"' in content,
        'aria-pressed' in content,
        m_overloaded_outage_timeline_use_case.init.call_count,
    ) == (True, True, 1, True, True, False, 1)


def test_journal_redirects_anonymous_to_login(f_anonymous_client: Client) -> None:
    response = f_anonymous_client.get('/journal/')

    assert (response.status_code, response['Location']) == (302, '/login/?next=/journal/')


def test_journal_words_the_settings_changed_in_the_panel(
    f_russian_client: Client,
    m_settings_timeline_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/journal/')

    content = response.content.decode()
    assert (
        [
            'Кухня: пол в комфорте 28° → 27,5°' in content,
            'Кухня, суббота: комфорт 08:00–23:30 → нет' in content,
            'Управление нагрузкой: порог отключения 24 А → 25 А' in content,
            'Бойлер 1 этаж: фаза неизвестна → L2' in content,
        ],
        content.count('#settings-2'),
        'неизвестное событие' in content.lower(),
        m_settings_timeline_use_case.init.call_count,
    ) == ([True] * 4, 5, False, 1)
