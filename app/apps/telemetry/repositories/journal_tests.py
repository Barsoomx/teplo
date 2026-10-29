from datetime import UTC, datetime, timedelta

import pytest

from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_entries() -> list[JournalEntry]:
    return [
        JournalEntryFactory.create(kind='teplo_started', created_at=NOW - timedelta(hours=1)),
        JournalEntryFactory.create(kind='zone_boosted', created_at=NOW),
    ]


def test_latest_returns_newest_first(f_entries: list[JournalEntry]) -> None:
    assert JournalRepository.latest(limit=1) == [f_entries[1]]


def test_last_activity_is_newest_entry_time(f_entries: list[JournalEntry]) -> None:
    assert JournalRepository.last_activity() == f_entries[1].created_at


def test_add_creates_entry() -> None:
    JournalRepository.add(kind='teplo_started', subject='', payload={'last_activity': None}, created_at=NOW)

    assert list(JournalEntry.objects.values_list('kind', 'payload', 'created_at')) == [
        ('teplo_started', {'last_activity': None}, NOW),
    ]
