from datetime import UTC, datetime, timedelta

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.services.presence_store import PresenceSnapshot, PresenceStore

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(seconds=30)


@pytest.fixture
def f_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def f_store(f_clock: MonotonicClockStub) -> PresenceStore:
    return PresenceStore(unavailable_after=timedelta(minutes=10), clock=f_clock)


@pytest.fixture
def f_reported_store(f_store: PresenceStore, f_clock: MonotonicClockStub) -> PresenceStore:
    f_store.record('pc-office', present=True, read_at=NOW)
    f_clock.advance(30)
    f_store.record('pc-office', present=False, read_at=LATER)
    return f_store


@pytest.fixture
def f_failed_store(f_reported_store: PresenceStore, f_clock: MonotonicClockStub) -> PresenceStore:
    f_clock.advance(30)
    f_reported_store.record_failure('pc-office', error_code='broker_unavailable', error='mqtt broker unreachable')
    return f_reported_store


def test_report_makes_the_source_available_and_starts_the_watch(
    f_store: PresenceStore,
    f_clock: MonotonicClockStub,
) -> None:
    f_store.record('pc-office', present=False, read_at=NOW)
    f_clock.advance(30)

    assert f_store.snapshot('pc-office') == PresenceSnapshot(
        slug='pc-office',
        present=False,
        read_at=NOW,
        age=timedelta(seconds=30),
        seen_age=None,
        watched_for=timedelta(seconds=30),
        available=True,
        error_code=None,
        error=None,
    )


def test_source_without_a_report_is_unavailable(f_store: PresenceStore) -> None:
    assert f_store.snapshot('pc-office') == PresenceSnapshot(
        slug='pc-office',
        present=None,
        read_at=None,
        age=None,
        seen_age=None,
        watched_for=None,
        available=False,
        error_code=None,
        error=None,
    )


@pytest.mark.parametrize(('silence', 'available'), [(600, True), (601, False)], ids=['ten_minutes', 'longer'])
def test_source_is_unavailable_once_its_report_is_older_than_ten_minutes(
    f_reported_store: PresenceStore,
    f_clock: MonotonicClockStub,
    silence: int,
    available: bool,
) -> None:
    f_clock.advance(silence)

    snapshot = f_reported_store.snapshot('pc-office')
    assert (snapshot.available, snapshot.watched_for is not None) == (available, available)


def test_failure_makes_the_source_unavailable_at_once(f_failed_store: PresenceStore) -> None:
    snapshot = f_failed_store.snapshot('pc-office')

    assert (snapshot.available, snapshot.watched_for, snapshot.error_code, snapshot.error) == (
        False,
        None,
        'broker_unavailable',
        'mqtt broker unreachable',
    )


def test_cleared_failure_leaves_the_source_unavailable_until_its_next_report(
    f_failed_store: PresenceStore,
    f_clock: MonotonicClockStub,
) -> None:
    f_failed_store.clear_failure('pc-office', 'broker_unavailable')
    cleared = f_failed_store.snapshot('pc-office')
    f_clock.advance(30)
    f_failed_store.record('pc-office', present=False, read_at=LATER + timedelta(minutes=1))
    reported = f_failed_store.snapshot('pc-office')

    assert (
        (cleared.available, cleared.error_code, cleared.error),
        (reported.available, reported.watched_for),
    ) == ((False, None, None), (True, timedelta(0)))


def test_clearing_another_failure_keeps_the_recorded_one(f_failed_store: PresenceStore) -> None:
    f_failed_store.clear_failure('pc-office', 'device_unavailable')

    assert f_failed_store.snapshot('pc-office').error_code == 'broker_unavailable'


def test_report_drops_the_failure(f_failed_store: PresenceStore) -> None:
    f_failed_store.record('pc-office', present=False, read_at=LATER + timedelta(minutes=1))

    snapshot = f_failed_store.snapshot('pc-office')
    assert (snapshot.available, snapshot.error_code, snapshot.error) == (True, None, None)


def test_last_sighting_lasts_across_reports_of_nobody_and_failures(
    f_failed_store: PresenceStore,
    f_clock: MonotonicClockStub,
) -> None:
    f_clock.advance(60)
    f_failed_store.record('pc-office', present=False, read_at=LATER + timedelta(minutes=1))

    assert f_failed_store.snapshot('pc-office').seen_age == timedelta(minutes=2)


def test_watch_goes_on_while_the_source_stays_available(
    f_reported_store: PresenceStore,
    f_clock: MonotonicClockStub,
) -> None:
    f_clock.advance(30)
    f_reported_store.record('pc-office', present=False, read_at=LATER + timedelta(seconds=30))

    assert f_reported_store.snapshot('pc-office').watched_for == timedelta(minutes=1)


def test_watch_starts_over_after_the_source_was_silent_for_ten_minutes(
    f_reported_store: PresenceStore,
    f_clock: MonotonicClockStub,
) -> None:
    f_clock.advance(601)
    f_reported_store.record('pc-office', present=False, read_at=LATER + timedelta(minutes=10))
    f_clock.advance(30)

    snapshot = f_reported_store.snapshot('pc-office')
    assert (snapshot.watched_for, snapshot.seen_age) == (timedelta(seconds=30), timedelta(seconds=661))


def test_store_remembers_the_availability_last_reported(f_store: PresenceStore) -> None:
    before = f_store.last_reported('pc-office')
    f_store.mark_reported('pc-office', available=False)

    assert (before, f_store.last_reported('pc-office')) == (None, False)
