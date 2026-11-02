from datetime import UTC, datetime, timedelta

import pytest

from apps.telemetry.domain.starts import TeploStart
from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
LAGGING_BOOT = NOW - timedelta(hours=5)
BOOT_ID = '5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e'
EARLIER_BOOT_ID = '0b7d3c1e-9a2f-4e6b-8c5d-3f1a2b4c6d8e'


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


@pytest.fixture
def starts_in_two_boots() -> None:
    JournalEntryFactory.create(
        kind='teplo_started',
        subject='',
        payload={'last_activity': '2026-10-27T08:00:00Z', 'boot_id': EARLIER_BOOT_ID, 'router_uptime': 86400.0},
        created_at=NOW - timedelta(days=2),
    )
    JournalEntryFactory.create(kind='zone_boosted', created_at=NOW - timedelta(days=1))
    JournalEntryFactory.create(
        kind='teplo_started',
        subject='',
        payload={'last_activity': '2026-10-29T05:30:00Z', 'boot_id': BOOT_ID, 'router_uptime': 60},
        created_at=LAGGING_BOOT,
    )
    JournalEntryFactory.create(
        kind='teplo_started',
        subject='',
        payload={'last_activity': '2026-10-29T05:30:00Z', 'boot_id': BOOT_ID, 'router_uptime': 1020.5},
        created_at=LAGGING_BOOT + timedelta(minutes=16),
    )


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


@pytest.mark.usefixtures('starts_in_two_boots')
def test_starts_are_read_newest_first_with_their_boot_and_router_uptime() -> None:
    assert list(JournalRepository.starts_newest_first()) == [
        TeploStart(
            boot_id=BOOT_ID,
            last_activity=datetime(2026, 10, 29, 5, 30, tzinfo=UTC),
            router_uptime=timedelta(seconds=1020.5),
        ),
        TeploStart(
            boot_id=BOOT_ID,
            last_activity=datetime(2026, 10, 29, 5, 30, tzinfo=UTC),
            router_uptime=timedelta(minutes=1),
        ),
        TeploStart(
            boot_id=EARLIER_BOOT_ID,
            last_activity=datetime(2026, 10, 27, 8, 0, tzinfo=UTC),
            router_uptime=timedelta(days=1),
        ),
    ]


def test_no_start_is_read_from_an_empty_journal() -> None:
    assert list(JournalRepository.starts_newest_first()) == []


@pytest.mark.usefixtures('unreadable_start')
@pytest.mark.parametrize(
    'unreadable_start',
    [
        {'last_activity': None},
        {'last_activity': None, 'boot_id': None, 'router_uptime': None},
        {'last_activity': None, 'boot_id': '', 'router_uptime': -1.0},
        {'last_activity': None, 'boot_id': 7, 'router_uptime': '60'},
        {'last_activity': None, 'boot_id': ['a'], 'router_uptime': True},
        {'last_activity': None, 'boot_id': None, 'router_uptime': 1e300},
        ['2026-10-29T05:59:59Z'],
    ],
    ids=['before_boot_ids', 'null', 'empty_and_negative', 'wrong_types', 'list_and_bool', 'endless', 'not_an_object'],
    indirect=True,
)
def test_start_without_a_readable_boot_id_or_uptime_reads_as_unknown() -> None:
    assert list(JournalRepository.starts_newest_first()) == [
        TeploStart(boot_id=None, last_activity=None, router_uptime=None),
    ]
