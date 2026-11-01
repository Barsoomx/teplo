from datetime import UTC, datetime, timedelta

import pytest

from apps.notifications.domain.outage import Outage, StartupNote, detect_outage

STARTED_AT = datetime(2026, 10, 31, 17, 33, tzinfo=UTC)
STARTED = 50.0


@pytest.mark.parametrize(
    ('down_for', 'reported'),
    [
        (timedelta(minutes=3), False),
        (timedelta(minutes=3, seconds=1), True),
        (timedelta(minutes=9), True),
    ],
)
def test_outage_is_reported_when_longer_than_three_minutes(down_for: timedelta, reported: bool) -> None:
    note = StartupNote(last_activity=STARTED_AT - down_for, monotonic=STARTED)

    outage = detect_outage(note, STARTED_AT, STARTED)

    assert outage == (Outage(began_at=STARTED_AT - down_for, ended_at=STARTED_AT) if reported else None)


def test_outage_ends_at_the_start_even_when_the_clock_is_trusted_later() -> None:
    note = StartupNote(last_activity=STARTED_AT - timedelta(minutes=9), monotonic=STARTED)

    outage = detect_outage(note, STARTED_AT + timedelta(minutes=7), STARTED + 420)

    assert outage == Outage(began_at=STARTED_AT - timedelta(minutes=9), ended_at=STARTED_AT)


def test_first_start_has_no_outage() -> None:
    assert detect_outage(StartupNote(last_activity=None, monotonic=STARTED), STARTED_AT, STARTED) is None


def test_activity_after_the_start_is_no_outage() -> None:
    note = StartupNote(last_activity=STARTED_AT + timedelta(hours=1), monotonic=STARTED)

    assert detect_outage(note, STARTED_AT, STARTED) is None
