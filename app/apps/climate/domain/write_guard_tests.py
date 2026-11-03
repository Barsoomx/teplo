from datetime import UTC, datetime, timedelta

import pytest

from apps.climate.domain.write_guard import WriteSlot, slot_free, write_allowed

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
MOMENT = 5000.0


@pytest.mark.parametrize(
    ('last_command_at', 'expected'),
    [
        (None, True),
        (NOW - timedelta(minutes=29), False),
        (NOW - timedelta(minutes=30), True),
    ],
)
def test_write_allowed_respects_thirty_minutes(last_command_at: datetime | None, expected: bool) -> None:
    assert write_allowed(last_command_at, NOW) is expected


def test_write_allowed_when_clock_moved_back_past_last_command() -> None:
    assert write_allowed(NOW + timedelta(hours=1), NOW) is True


@pytest.mark.parametrize(
    ('stamped_at', 'expected'),
    [
        (None, True),
        (NOW - timedelta(minutes=29), False),
        (NOW - timedelta(minutes=30), True),
        (NOW + timedelta(hours=1), True),
    ],
    ids=['never_written', 'written_within_thirty_minutes', 'written_thirty_minutes_ago', 'stamped_after_the_clock'],
)
def test_slot_nobody_took_since_the_start_follows_the_stamp_the_caller_read(
    stamped_at: datetime | None,
    expected: bool,
) -> None:
    assert slot_free(None, stamped_at=stamped_at, now=NOW, moment=MOMENT) is expected


@pytest.mark.parametrize(
    ('slot', 'expected'),
    [
        (WriteSlot(at=NOW - timedelta(minutes=29), moment=MOMENT - 1740), False),
        (WriteSlot(at=NOW - timedelta(minutes=30), moment=MOMENT - 60), True),
    ],
    ids=['within_thirty_minutes_of_the_stamp', 'thirty_minutes_after_the_stamp_whatever_the_monotonic_clock'],
)
def test_slot_whose_stamp_the_caller_read_follows_that_stamp_as_before(slot: WriteSlot, expected: bool) -> None:
    assert slot_free(slot, stamped_at=slot.at, now=NOW, moment=MOMENT) is expected


@pytest.mark.parametrize(
    ('slot', 'stamped_at', 'expected'),
    [
        (WriteSlot(at=NOW - timedelta(minutes=2), moment=MOMENT - 120), NOW - timedelta(hours=1), False),
        (WriteSlot(at=NOW - timedelta(minutes=2), moment=MOMENT - 1800), NOW - timedelta(hours=1), True),
        (WriteSlot(at=NOW + timedelta(seconds=20), moment=MOMENT - 5), NOW + timedelta(seconds=20), False),
        (WriteSlot(at=NOW - timedelta(minutes=10), moment=MOMENT - 600), None, False),
    ],
    ids=[
        'taken_after_the_caller_read_an_older_stamp',
        'taken_thirty_minutes_ago_on_the_monotonic_clock',
        'taken_after_the_moment_the_caller_writes_for',
        'taken_while_the_stamp_the_caller_read_was_cleared',
    ],
)
def test_slot_the_caller_has_not_seen_waits_thirty_minutes_on_the_monotonic_clock(
    slot: WriteSlot,
    stamped_at: datetime | None,
    expected: bool,
) -> None:
    assert slot_free(slot, stamped_at=stamped_at, now=NOW, moment=MOMENT) is expected
