from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
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

pytestmark = pytest.mark.django_db

MORNING = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
RAW_DETAILS = ('timed out after 5 s', 'Network Error: Device Unreachable', 'thermostat did not answer', 'teplo_stopped')
PLUG_DETAIL = 'unexpected plug payload: {}'


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


def test_journal_redirects_anonymous_to_login(f_anonymous_client: Client) -> None:
    response = f_anonymous_client.get('/journal/')

    assert (response.status_code, response['Location']) == (302, '/login/?next=/journal/')
