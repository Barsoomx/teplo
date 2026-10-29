from datetime import UTC, datetime, timedelta

import pytest

from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository
from apps.telemetry.usecases.list_journal_entries import (
    JournalEntryDTO,
    ListJournalEntriesInputDTO,
    ListJournalEntriesOutputDTO,
    ListJournalEntriesUseCase,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_entries() -> list[JournalEntry]:
    return [
        JournalEntryFactory.create(kind='teplo_started', subject='', created_at=NOW - timedelta(hours=1)),
        JournalEntryFactory.create(
            kind='zone_boosted',
            subject='kitchen',
            payload={'until': '2026-10-29T08:00:00Z'},
            created_at=NOW,
        ),
    ]


@pytest.fixture
def f_use_case() -> ListJournalEntriesUseCase:
    return ListJournalEntriesUseCase(journal=JournalRepository())


def test_list_returns_newest_entries_up_to_limit(
    f_use_case: ListJournalEntriesUseCase,
    f_entries: list[JournalEntry],
) -> None:
    output = f_use_case.execute(ListJournalEntriesInputDTO(limit=1))

    assert output == ListJournalEntriesOutputDTO(
        entries=(
            JournalEntryDTO(
                kind='zone_boosted',
                subject='kitchen',
                payload={'until': '2026-10-29T08:00:00Z'},
                created_at=f_entries[1].created_at,
            ),
        ),
    )
