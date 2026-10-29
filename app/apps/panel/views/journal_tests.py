from collections.abc import Iterator
from datetime import UTC, date, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_journal_timeline import (
    GetJournalTimelineInputDTO,
    GetJournalTimelineOutputDTO,
    TimelineDay,
    TimelineEntry,
)

pytestmark = pytest.mark.django_db


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
                            payload={'change': {'power': None, 'mode': None, 'setpoint': '28.0'}, 'dry_run': True},
                            created_at=datetime(2026, 10, 29, 6, 0, tzinfo=UTC),
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


def test_journal_redirects_anonymous_to_login(f_anonymous_client: Client) -> None:
    response = f_anonymous_client.get('/journal/')

    assert (response.status_code, response['Location']) == (302, '/login/?next=/journal/')
