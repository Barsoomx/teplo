from datetime import UTC, datetime, timedelta

import pytest

from apps.climate.domain.write_guard import WriteSlot, override_holds, slot_free, write_allowed

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
MOMENT = 5000.0
READ_AT = MOMENT - 30
STAMPS = (
    None,
    NOW - timedelta(hours=2),
    NOW - timedelta(minutes=30),
    NOW - timedelta(minutes=29),
    NOW - timedelta(seconds=10),
    NOW + timedelta(seconds=5),
    NOW + timedelta(hours=1),
)


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
    ('stored_at', 'expected'),
    [(None, False), (READ_AT - 1, True), (READ_AT, True), (READ_AT + 1, False)],
    ids=['stamp_not_stored', 'stored_before_the_read', 'stored_as_the_writer_read', 'stored_after_the_read'],
)
def test_slot_is_read_by_a_writer_that_read_the_stamps_once_its_stamp_was_stored(
    stored_at: float | None,
    expected: bool,
) -> None:
    slot = WriteSlot(generation=1, at=NOW, moment=READ_AT - 5, stored_at=stored_at)

    assert slot.read_by(READ_AT) is expected


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
def test_slot_nobody_took_since_the_start_follows_the_stamp_the_writer_read(
    stamped_at: datetime | None,
    expected: bool,
) -> None:
    assert slot_free(None, stamped_at=stamped_at, now=NOW, read_at=READ_AT, moment=MOMENT) is expected


@pytest.mark.parametrize('stamped_at', STAMPS)
@pytest.mark.parametrize(
    'slot',
    [
        WriteSlot(generation=1, at=NOW - timedelta(minutes=29), moment=MOMENT - 1740, stored_at=MOMENT - 1739),
        WriteSlot(generation=1, at=NOW - timedelta(minutes=30), moment=MOMENT - 60, stored_at=MOMENT - 59),
        WriteSlot(generation=1, at=NOW + timedelta(seconds=5), moment=MOMENT - 65, stored_at=READ_AT),
        WriteSlot(generation=1, at=NOW + timedelta(hours=1), moment=MOMENT - 120, stored_at=MOMENT - 119),
    ],
    ids=[
        'stamped_and_stored_long_before',
        'stamped_a_minute_ago_by_a_clock_stepped_forward_since',
        'stored_as_the_writer_read',
        'stamped_later_than_the_clock_reads_since_it_was_set_back',
    ],
)
def test_ordinary_slot_the_writer_read_judges_by_its_stamp_exactly_as_master_did(
    slot: WriteSlot,
    stamped_at: datetime | None,
) -> None:
    assert slot_free(slot, stamped_at=stamped_at, now=NOW, read_at=READ_AT, moment=MOMENT) is write_allowed(
        stamped_at,
        NOW,
    )


@pytest.mark.parametrize(
    ('slot', 'expected'),
    [
        (
            WriteSlot(generation=1, at=NOW - timedelta(hours=2), moment=MOMENT - 180, override=True, stored_at=READ_AT),
            False,
        ),
        (
            WriteSlot(generation=1, at=NOW + timedelta(hours=1), moment=MOMENT - 180, override=True, stored_at=READ_AT),
            False,
        ),
        (
            WriteSlot(
                generation=1,
                at=NOW - timedelta(minutes=29),
                moment=MOMENT - 1800,
                override=True,
                stored_at=READ_AT,
            ),
            False,
        ),
        (
            WriteSlot(
                generation=1,
                at=NOW - timedelta(minutes=30),
                moment=MOMENT - 1800,
                override=True,
                stored_at=READ_AT,
            ),
            True,
        ),
    ],
    ids=[
        'stamped_hours_ago_by_a_clock_stepped_forward_since',
        'stamped_later_than_the_clock_reads_since_it_was_set_back',
        'within_thirty_minutes_of_the_stamp',
        'thirty_minutes_after_the_shed_on_both_clocks',
    ],
)
def test_shed_slot_whose_stamp_the_writer_read_holds_thirty_minutes_on_the_monotonic_clock_as_well(
    slot: WriteSlot,
    expected: bool,
) -> None:
    assert slot_free(slot, stamped_at=slot.at, now=NOW, read_at=READ_AT, moment=MOMENT) is expected


@pytest.mark.parametrize('stamped_at', STAMPS)
@pytest.mark.parametrize(
    ('slot', 'expected'),
    [
        (WriteSlot(generation=1, at=NOW - timedelta(minutes=2), moment=MOMENT - 120), False),
        (WriteSlot(generation=1, at=NOW - timedelta(minutes=2), moment=MOMENT - 1800), True),
        (WriteSlot(generation=1, at=NOW + timedelta(seconds=20), moment=MOMENT - 5, stored_at=MOMENT - 4), False),
        (WriteSlot(generation=1, at=NOW - timedelta(seconds=40), moment=READ_AT - 10, stored_at=READ_AT + 1), False),
        (
            WriteSlot(generation=1, at=NOW, moment=MOMENT - 10, override=True, stored_at=MOMENT - 9),
            False,
        ),
    ],
    ids=[
        'stamp_still_being_stored',
        'stamp_never_stored_thirty_minutes_ago_on_the_monotonic_clock',
        'stored_after_the_writer_took_its_time',
        'taken_before_the_writer_took_its_time_and_stored_after',
        'shed_stored_after_the_writer_took_its_time',
    ],
)
def test_slot_the_writer_has_not_read_waits_thirty_minutes_on_the_monotonic_clock_whatever_the_stamps(
    slot: WriteSlot,
    stamped_at: datetime | None,
    expected: bool,
) -> None:
    assert slot_free(slot, stamped_at=stamped_at, now=NOW, read_at=READ_AT, moment=MOMENT) is expected


@pytest.mark.parametrize(
    ('slot', 'expected'),
    [
        (None, False),
        (WriteSlot(generation=1, at=NOW, moment=MOMENT - 60), False),
        (WriteSlot(generation=1, at=NOW, moment=MOMENT - 1799, override=True), True),
        (WriteSlot(generation=1, at=NOW, moment=MOMENT - 1800, override=True), False),
    ],
    ids=['no_write', 'an_ordinary_write', 'shed_within_thirty_minutes', 'shed_thirty_minutes_ago'],
)
def test_override_holds_its_zone_for_thirty_minutes_on_the_monotonic_clock(
    slot: WriteSlot | None,
    expected: bool,
) -> None:
    assert override_holds(slot, MOMENT) is expected
