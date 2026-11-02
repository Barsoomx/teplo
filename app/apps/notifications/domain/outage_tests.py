from datetime import UTC, datetime, timedelta

import pytest

from apps.notifications.domain.outage import Outage, StartupNote, detect_outage, started_before_last_activity

STARTED_AT = datetime(2026, 10, 31, 17, 33, tzinfo=UTC)
STARTED = 50.0
ROUTER_UP_FOR_DAYS = timedelta(days=3)
ROUTER_JUST_BOOTED = timedelta(minutes=2)


@pytest.mark.parametrize(
    ('down_for', 'reported'),
    [
        (timedelta(minutes=3), False),
        (timedelta(minutes=3, seconds=1), True),
        (timedelta(minutes=9), True),
    ],
)
def test_outage_is_reported_when_longer_than_three_minutes(down_for: timedelta, reported: bool) -> None:
    note = StartupNote(last_activity=STARTED_AT - down_for, monotonic=STARTED, router_uptime=ROUTER_UP_FOR_DAYS)

    outage = detect_outage(note, STARTED_AT, STARTED)

    assert outage == (
        Outage(began_at=STARTED_AT - down_for, ended_at=STARTED_AT, power_cut=False) if reported else None
    )


def test_outage_ends_at_the_start_even_when_the_clock_is_trusted_later() -> None:
    note = StartupNote(
        last_activity=STARTED_AT - timedelta(minutes=9),
        monotonic=STARTED,
        router_uptime=ROUTER_UP_FOR_DAYS,
    )

    outage = detect_outage(note, STARTED_AT + timedelta(minutes=7), STARTED + 420)

    assert outage == Outage(began_at=STARTED_AT - timedelta(minutes=9), ended_at=STARTED_AT, power_cut=False)


def test_first_start_has_no_outage() -> None:
    note = StartupNote(last_activity=None, monotonic=STARTED, router_uptime=ROUTER_UP_FOR_DAYS)

    assert detect_outage(note, STARTED_AT, STARTED) is None


def test_activity_after_the_start_is_no_outage() -> None:
    note = StartupNote(
        last_activity=STARTED_AT + timedelta(hours=1),
        monotonic=STARTED,
        router_uptime=ROUTER_UP_FOR_DAYS,
    )

    assert detect_outage(note, STARTED_AT, STARTED) is None


@pytest.mark.parametrize(
    ('router_uptime', 'power_cut'),
    [
        (timedelta(minutes=9, seconds=59), True),
        (timedelta(minutes=10), False),
        (ROUTER_UP_FOR_DAYS, False),
        (None, False),
    ],
    ids=['router_up_under_ten_minutes', 'router_up_ten_minutes', 'router_up_for_days', 'uptime_unknown'],
)
def test_start_follows_a_power_cut_only_when_the_router_came_up_under_ten_minutes_before(
    router_uptime: timedelta | None,
    power_cut: bool,
) -> None:
    note = StartupNote(last_activity=STARTED_AT, monotonic=STARTED, router_uptime=router_uptime)

    assert note.follows_power_cut is power_cut


def test_power_cut_is_reported_however_short_the_gap() -> None:
    note = StartupNote(
        last_activity=STARTED_AT - timedelta(minutes=2),
        monotonic=STARTED,
        router_uptime=ROUTER_JUST_BOOTED,
    )

    outage = detect_outage(note, STARTED_AT, STARTED)

    assert outage == Outage(began_at=STARTED_AT - timedelta(minutes=2), ended_at=STARTED_AT, power_cut=True)


def test_restart_after_more_than_three_minutes_is_reported_as_no_power_cut() -> None:
    note = StartupNote(
        last_activity=STARTED_AT - timedelta(minutes=4),
        monotonic=STARTED,
        router_uptime=ROUTER_UP_FOR_DAYS,
    )

    outage = detect_outage(note, STARTED_AT, STARTED)

    assert outage == Outage(began_at=STARTED_AT - timedelta(minutes=4), ended_at=STARTED_AT, power_cut=False)


def test_unknown_uptime_reports_a_short_gap_as_no_outage() -> None:
    note = StartupNote(last_activity=STARTED_AT - timedelta(minutes=2), monotonic=STARTED, router_uptime=None)

    assert detect_outage(note, STARTED_AT, STARTED) is None


@pytest.mark.parametrize(
    'last_activity',
    [None, STARTED_AT + timedelta(minutes=1)],
    ids=['first_start', 'activity_after_the_start'],
)
def test_power_cut_needs_a_last_activity_before_the_start(last_activity: datetime | None) -> None:
    note = StartupNote(last_activity=last_activity, monotonic=STARTED, router_uptime=ROUTER_JUST_BOOTED)

    assert detect_outage(note, STARTED_AT, STARTED) is None


@pytest.mark.parametrize(
    ('last_activity', 'behind'),
    [
        (None, False),
        (STARTED_AT - timedelta(minutes=9), False),
        (STARTED_AT, False),
        (STARTED_AT + timedelta(seconds=1), True),
    ],
)
def test_start_precedes_only_a_last_activity_stamped_after_it(
    last_activity: datetime | None,
    behind: bool,
) -> None:
    note = StartupNote(last_activity=last_activity, monotonic=STARTED, router_uptime=ROUTER_UP_FOR_DAYS)

    assert started_before_last_activity(note, STARTED_AT + timedelta(minutes=7), STARTED + 420) is behind
