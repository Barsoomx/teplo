from datetime import UTC, datetime, timedelta

import pytest

from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
LAGGING_BOOT = NOW - timedelta(hours=5)


@pytest.fixture
def f_entries() -> list[JournalEntry]:
    return [
        JournalEntryFactory.create(kind='teplo_started', created_at=NOW - timedelta(hours=1)),
        JournalEntryFactory.create(kind='zone_boosted', created_at=NOW),
    ]


@pytest.fixture
def starts_before_and_after_a_boot_on_a_lagging_clock() -> None:
    JournalEntryFactory.create(
        kind='teplo_started',
        subject='',
        payload={'last_activity': '2026-10-29T04:50:00Z'},
        created_at=NOW - timedelta(hours=1),
    )
    JournalEntryFactory.create(
        kind='teplo_started',
        subject='',
        payload={'last_activity': '2026-10-29T05:59:59.982Z'},
        created_at=LAGGING_BOOT,
    )
    JournalEntryFactory.create(kind='zone_boosted', created_at=LAGGING_BOOT + timedelta(minutes=5))


@pytest.fixture
def unreadable_start(request: pytest.FixtureRequest) -> None:
    JournalEntryFactory.create(kind='teplo_started', subject='', payload=request.param, created_at=NOW)


def test_latest_returns_newest_first(f_entries: list[JournalEntry]) -> None:
    assert JournalRepository.latest(limit=1) == [f_entries[1]]


def test_last_activity_is_newest_entry_time(f_entries: list[JournalEntry]) -> None:
    assert JournalRepository.last_activity() == f_entries[1].created_at


def test_add_creates_entry() -> None:
    JournalRepository.add(kind='teplo_started', subject='', payload={'last_activity': None}, created_at=NOW)

    assert list(JournalEntry.objects.values_list('kind', 'payload', 'created_at')) == [
        ('teplo_started', {'last_activity': None}, NOW),
    ]


@pytest.mark.usefixtures('starts_before_and_after_a_boot_on_a_lagging_clock')
def test_previous_start_last_activity_comes_from_the_newest_start_by_row_id() -> None:
    assert JournalRepository.previous_start_last_activity() == datetime(2026, 10, 29, 5, 59, 59, 982000, tzinfo=UTC)


def test_previous_start_last_activity_is_none_without_a_start() -> None:
    assert JournalRepository.previous_start_last_activity() is None


@pytest.mark.usefixtures('unreadable_start')
@pytest.mark.parametrize(
    'unreadable_start',
    [
        {},
        {'last_activity': None},
        {'last_activity': 'yesterday'},
        {'last_activity': '2026-10-29T05:59:59'},
        ['2026-10-29T05:59:59Z'],
    ],
    ids=['missing', 'null', 'malformed', 'without_a_time_zone', 'not_an_object'],
    indirect=True,
)
def test_previous_start_last_activity_is_none_when_unreadable() -> None:
    assert JournalRepository.previous_start_last_activity() is None
