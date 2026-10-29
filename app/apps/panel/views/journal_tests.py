from collections.abc import Iterator
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.telemetry.usecases.list_journal_entries import (
    JournalEntryDTO,
    ListJournalEntriesInputDTO,
    ListJournalEntriesOutputDTO,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_list_journal_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal.ListJournalEntriesUseCase') as use_case:
        use_case.init.return_value.execute.return_value = ListJournalEntriesOutputDTO(
            entries=(
                JournalEntryDTO(
                    kind='zone_boosted',
                    subject='kitchen',
                    payload={'until': '2026-10-29T08:00:00Z'},
                    created_at=datetime(2026, 10, 29, 6, 0, tzinfo=UTC),
                ),
            ),
        )
        yield use_case


def test_journal_lists_latest_entries(f_client: Client, m_list_journal_use_case: MagicMock) -> None:
    response = f_client.get('/journal/')

    assert (
        response.status_code,
        'zone_boosted' in response.content.decode(),
        m_list_journal_use_case.init.return_value.execute.call_args.args,
    ) == (200, True, (ListJournalEntriesInputDTO(limit=100),))
