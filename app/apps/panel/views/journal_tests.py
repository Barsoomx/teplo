from collections.abc import Iterator
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.telemetry.models import JournalEntry

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_journal_repository() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal.JournalRepository') as repository:
        repository.latest.return_value = [
            JournalEntry(
                kind='zone_boosted',
                subject='kitchen',
                payload={'until': '2026-10-29T08:00:00Z'},
                created_at=datetime(2026, 10, 29, 6, 0, tzinfo=UTC),
            ),
        ]
        yield repository


def test_journal_lists_latest_entries(f_client: Client, m_journal_repository: MagicMock) -> None:
    response = f_client.get('/journal/')

    assert (
        response.status_code,
        'zone_boosted' in response.content.decode(),
        m_journal_repository.latest.call_args.kwargs,
    ) == (200, True, {'limit': 100})
