from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from django.db import OperationalError

from apps.climate.domain.write_guard import WriteReservation, WriteSlot
from apps.climate.services.write_gate import ZoneWriteGate
from apps.core.stubs import MonotonicClockStub

NOW = datetime(2026, 11, 3, 7, 0, tzinfo=UTC)
START = 1000.0
HALF_HOUR = timedelta(minutes=30)
STORED = START + 2
SHED_AT = NOW + timedelta(seconds=10)


def _living_room_written(gate: ZoneWriteGate) -> None:
    gate.reserve('living-room', NOW, stamped_at=None, read_at=START)


def _kitchen_shed(gate: ZoneWriteGate) -> None:
    gate.override('kitchen', SHED_AT)


def _kitchen_boosted(gate: ZoneWriteGate) -> None:
    gate.clear('kitchen')


def _stored(gate: ZoneWriteGate, reservation: WriteReservation) -> None:
    with gate.stamping(reservation):
        pass


def _refused(gate: ZoneWriteGate, reservation: WriteReservation) -> None:
    with pytest.raises(OperationalError), gate.stamping(reservation):
        raise OperationalError('database is locked')


@pytest.fixture
def f_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=START)


@pytest.fixture
def f_gate(f_clock: MonotonicClockStub) -> ZoneWriteGate:
    return ZoneWriteGate(clock=f_clock)


@pytest.fixture
def f_reservation(f_gate: ZoneWriteGate) -> WriteReservation:
    reservation = f_gate.reserve('kitchen', NOW, stamped_at=None, read_at=START)
    assert reservation is not None
    return reservation


@pytest.fixture
def f_stored_reservation(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    f_reservation: WriteReservation,
) -> WriteReservation:
    f_clock.advance(STORED - START)
    _stored(f_gate, f_reservation)
    return f_reservation


@pytest.fixture
def f_reservation_half_an_hour_later(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    f_stored_reservation: WriteReservation,
) -> WriteReservation:
    f_clock.advance(HALF_HOUR.total_seconds())
    reservation = f_gate.reserve(
        'kitchen',
        NOW + HALF_HOUR,
        stamped_at=f_stored_reservation.slot.at,
        read_at=f_clock(),
    )
    assert reservation is not None
    return reservation


@pytest.fixture
def f_reservation_after_another_write(
    request: pytest.FixtureRequest,
    f_gate: ZoneWriteGate,
    f_reservation: WriteReservation,
) -> WriteReservation:
    meanwhile: Callable[[ZoneWriteGate], None] = request.param
    meanwhile(f_gate)
    return f_reservation


@pytest.fixture
def f_shed(f_gate: ZoneWriteGate, f_clock: MonotonicClockStub) -> WriteReservation:
    f_clock.advance(10)
    shed = f_gate.override('kitchen', SHED_AT)
    _stored(f_gate, shed)
    return shed


@pytest.fixture
def f_reservation_overridden_by_a_shed(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    f_reservation: WriteReservation,
) -> WriteReservation:
    f_clock.advance(10)
    f_gate.override('kitchen', SHED_AT)
    return f_reservation


def test_gate_reserves_the_slot_of_a_zone_never_written(f_gate: ZoneWriteGate) -> None:
    assert f_gate.reserve('kitchen', NOW, stamped_at=None, read_at=START) == WriteReservation(
        zone='kitchen',
        slot=WriteSlot(generation=1, at=NOW, moment=START),
        previous=None,
    )


@pytest.mark.parametrize(
    ('seconds', 'expected'),
    [(1799, False), (1800, True)],
    ids=['within_thirty_minutes', 'thirty_minutes_later'],
)
@pytest.mark.usefixtures('f_reservation')
def test_slot_whose_stamp_is_not_stored_yet_holds_every_writer_thirty_minutes_on_the_monotonic_clock(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    seconds: int,
    expected: bool,
) -> None:
    f_clock.advance(seconds)

    reservation = f_gate.reserve('kitchen', NOW + HALF_HOUR, stamped_at=None, read_at=f_clock())

    assert (reservation is not None) is expected


@pytest.mark.parametrize(
    ('later', 'stamped_at', 'expected'),
    [
        (HALF_HOUR - timedelta(seconds=1), NOW, False),
        (HALF_HOUR, NOW, True),
        (timedelta(minutes=1), None, True),
    ],
    ids=['within_thirty_minutes_of_the_stamp', 'thirty_minutes_after_the_stamp', 'stamp_cleared_by_a_boost'],
)
@pytest.mark.usefixtures('f_stored_reservation')
def test_writer_that_read_the_stamps_after_the_slot_was_stored_follows_the_stamp_it_read_as_before(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    later: timedelta,
    stamped_at: datetime | None,
    expected: bool,
) -> None:
    f_clock.advance(60)

    reservation = f_gate.reserve('kitchen', NOW + later, stamped_at=stamped_at, read_at=f_clock())

    assert (reservation is not None) is expected


@pytest.mark.usefixtures('f_stored_reservation')
def test_writer_that_read_the_stamps_after_the_slot_was_stored_reads_a_stamp_later_than_a_clock_set_back_as_before(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
) -> None:
    f_clock.advance(60)

    assert f_gate.reserve('kitchen', NOW - timedelta(seconds=10), stamped_at=NOW, read_at=f_clock()) == (
        WriteReservation(
            zone='kitchen',
            slot=WriteSlot(generation=2, at=NOW - timedelta(seconds=10), moment=STORED + 60),
            previous=WriteSlot(generation=1, at=NOW, moment=START, stored_at=STORED),
        )
    )


@pytest.mark.parametrize(
    ('seconds', 'expected'),
    [(1797, False), (1798, True)],
    ids=['within_thirty_minutes_of_the_slot', 'thirty_minutes_after_the_slot'],
)
@pytest.mark.usefixtures('f_stored_reservation')
def test_writer_that_took_its_time_before_the_slot_was_stored_waits_thirty_minutes_on_the_monotonic_clock(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    seconds: int,
    expected: bool,
) -> None:
    f_clock.advance(seconds)

    reservation = f_gate.reserve('kitchen', NOW - timedelta(seconds=5), stamped_at=NOW, read_at=START + 1)

    assert (reservation is not None) is expected


@pytest.mark.usefixtures('f_stored_reservation')
def test_writer_holds_no_other_zone(f_gate: ZoneWriteGate) -> None:
    assert f_gate.reserve('living-room', NOW, stamped_at=None, read_at=START) == WriteReservation(
        zone='living-room',
        slot=WriteSlot(generation=2, at=NOW, moment=STORED),
        previous=None,
    )


@pytest.mark.parametrize(
    ('seconds', 'expected'),
    [(1799, False), (1800, True)],
    ids=['within_thirty_minutes_of_the_shed', 'thirty_minutes_after_the_shed'],
)
def test_writer_that_read_the_stamp_of_a_shed_waits_thirty_minutes_on_the_monotonic_clock_whatever_the_clock_reads(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    f_shed: WriteReservation,
    seconds: int,
    expected: bool,
) -> None:
    f_clock.advance(seconds)

    reservation = f_gate.reserve('kitchen', NOW + timedelta(hours=2), stamped_at=f_shed.slot.at, read_at=f_clock())

    assert (reservation is not None) is expected


@pytest.mark.usefixtures('f_stored_reservation')
def test_shed_takes_the_slot_whatever_the_budget(f_gate: ZoneWriteGate) -> None:
    assert f_gate.override('kitchen', SHED_AT) == WriteReservation(
        zone='kitchen',
        slot=WriteSlot(generation=2, at=SHED_AT, moment=STORED, override=True),
        previous=WriteSlot(generation=1, at=NOW, moment=START, stored_at=STORED),
    )


def test_refused_stamp_hands_the_slot_back_to_the_write_before_it(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    f_reservation_half_an_hour_later: WriteReservation,
) -> None:
    _refused(f_gate, f_reservation_half_an_hour_later)

    assert f_gate.reserve('kitchen', NOW + HALF_HOUR, stamped_at=NOW, read_at=f_clock()) == WriteReservation(
        zone='kitchen',
        slot=WriteSlot(generation=3, at=NOW + HALF_HOUR, moment=STORED + 1800),
        previous=WriteSlot(generation=1, at=NOW, moment=START, stored_at=STORED),
    )


def test_refused_stamp_of_the_first_write_leaves_the_zone_free(
    f_gate: ZoneWriteGate,
    f_reservation: WriteReservation,
) -> None:
    _refused(f_gate, f_reservation)

    assert f_gate.reserve('kitchen', NOW, stamped_at=None, read_at=START) == WriteReservation(
        zone='kitchen',
        slot=WriteSlot(generation=2, at=NOW, moment=START),
        previous=None,
    )


def test_refused_stamp_keeps_a_slot_a_shed_took_meanwhile(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    f_reservation_overridden_by_a_shed: WriteReservation,
) -> None:
    _refused(f_gate, f_reservation_overridden_by_a_shed)
    f_clock.advance(1800)

    assert f_gate.reserve('kitchen', NOW + timedelta(minutes=31), stamped_at=None, read_at=f_clock()) == (
        WriteReservation(
            zone='kitchen',
            slot=WriteSlot(generation=3, at=NOW + timedelta(minutes=31), moment=START + 1810),
            previous=WriteSlot(generation=2, at=SHED_AT, moment=START + 10, override=True),
        )
    )


def test_stamp_stored_for_a_write_a_shed_overtook_marks_nothing(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    f_reservation_overridden_by_a_shed: WriteReservation,
) -> None:
    _stored(f_gate, f_reservation_overridden_by_a_shed)
    f_clock.advance(1800)

    reservation = f_gate.reserve('kitchen', SHED_AT + timedelta(minutes=1), stamped_at=SHED_AT, read_at=f_clock())

    assert reservation is not None


@pytest.mark.parametrize(
    ('f_reservation_after_another_write', 'expected'),
    [(_living_room_written, True), (_kitchen_shed, False), (_kitchen_boosted, True)],
    ids=['another_zone_written', 'a_shed_took_the_slot', 'a_boost_freed_the_slot'],
    indirect=['f_reservation_after_another_write'],
)
def test_reservation_holds_until_another_write_takes_the_slot(
    f_gate: ZoneWriteGate,
    f_reservation_after_another_write: WriteReservation,
    expected: bool,
) -> None:
    assert f_gate.holds(f_reservation_after_another_write) is expected


def test_stored_slot_still_holds_its_reservation(f_gate: ZoneWriteGate, f_stored_reservation: WriteReservation) -> None:
    assert f_gate.holds(f_stored_reservation) is True


@pytest.mark.usefixtures('f_reservation')
def test_cleared_zone_takes_its_next_write_at_once(f_gate: ZoneWriteGate) -> None:
    freed = f_gate.clear('kitchen')

    assert (freed, f_gate.reserve('kitchen', NOW + timedelta(minutes=1), stamped_at=None, read_at=START)) == (
        True,
        WriteReservation(
            zone='kitchen',
            slot=WriteSlot(generation=2, at=NOW + timedelta(minutes=1), moment=START),
            previous=None,
        ),
    )


@pytest.mark.parametrize(
    ('seconds', 'expected'),
    [
        (1799, (False, None)),
        (
            1800,
            (
                True,
                WriteReservation(
                    zone='kitchen',
                    slot=WriteSlot(generation=3, at=NOW + HALF_HOUR, moment=START + 1810),
                    previous=None,
                ),
            ),
        ),
    ],
    ids=['within_thirty_minutes_of_the_shed', 'thirty_minutes_after_the_shed'],
)
@pytest.mark.usefixtures('f_reservation_overridden_by_a_shed')
def test_clear_keeps_the_slot_of_a_shed_for_thirty_minutes_on_the_monotonic_clock_and_says_so(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    seconds: int,
    expected: tuple[bool, WriteReservation | None],
) -> None:
    f_clock.advance(seconds)

    freed = f_gate.clear('kitchen')

    assert (freed, f_gate.reserve('kitchen', NOW + HALF_HOUR, stamped_at=None, read_at=f_clock())) == expected
