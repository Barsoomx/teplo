from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from apps.climate.domain.write_guard import WriteReservation, WriteSlot
from apps.climate.services.write_gate import ZoneWriteGate
from apps.core.stubs import MonotonicClockStub, WallClockStub

NOW = datetime(2026, 11, 3, 7, 0, tzinfo=UTC)
START = 1000.0
HALF_HOUR = timedelta(minutes=30)
WRITTEN = WriteSlot(at=NOW, moment=START)
OVERRIDDEN = WriteSlot(at=NOW + timedelta(seconds=10), moment=START + 10, override=True)


def _living_room_written(gate: ZoneWriteGate) -> None:
    gate.reserve('living-room', NOW, stamped_at=None)


def _kitchen_shed(gate: ZoneWriteGate) -> None:
    gate.override('kitchen', OVERRIDDEN.at)


def _kitchen_boosted(gate: ZoneWriteGate) -> None:
    gate.clear('kitchen')


@pytest.fixture
def f_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=START)


@pytest.fixture
def f_wall_clock() -> WallClockStub:
    return WallClockStub(now=NOW)


@pytest.fixture
def f_gate(f_clock: MonotonicClockStub, f_wall_clock: WallClockStub) -> ZoneWriteGate:
    return ZoneWriteGate(clock=f_clock, wall_clock=f_wall_clock)


@pytest.fixture
def f_reservation(f_gate: ZoneWriteGate) -> WriteReservation:
    reservation = f_gate.reserve('kitchen', NOW, stamped_at=None)
    assert reservation is not None
    return reservation


@pytest.fixture
def f_reservation_half_an_hour_later(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    f_reservation: WriteReservation,
) -> WriteReservation:
    f_clock.advance(HALF_HOUR.total_seconds())
    reservation = f_gate.reserve('kitchen', NOW + HALF_HOUR, stamped_at=f_reservation.slot.at)
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
def f_reservation_overridden_by_a_shed(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    f_reservation: WriteReservation,
) -> WriteReservation:
    f_clock.advance(10)
    f_gate.override('kitchen', OVERRIDDEN.at)
    return f_reservation


def test_gate_reserves_the_slot_of_a_zone_never_written(f_gate: ZoneWriteGate) -> None:
    assert f_gate.reserve('kitchen', NOW, stamped_at=None) == WriteReservation(
        zone='kitchen',
        slot=WRITTEN,
        previous=None,
    )


@pytest.mark.parametrize(
    ('seconds', 'expected'),
    [
        (1799, None),
        (
            1800,
            WriteReservation(zone='kitchen', slot=WriteSlot(at=NOW + HALF_HOUR, moment=START + 1800), previous=WRITTEN),
        ),
    ],
    ids=['within_thirty_minutes', 'thirty_minutes_later'],
)
@pytest.mark.usefixtures('f_reservation')
def test_reserved_slot_holds_a_writer_that_read_an_older_stamp_for_thirty_minutes_on_the_monotonic_clock(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    seconds: int,
    expected: WriteReservation | None,
) -> None:
    f_clock.advance(seconds)

    assert f_gate.reserve('kitchen', NOW + timedelta(seconds=seconds), stamped_at=NOW - timedelta(hours=1)) == expected


@pytest.mark.parametrize(
    ('later', 'allowed'),
    [(HALF_HOUR - timedelta(seconds=1), False), (HALF_HOUR, True)],
    ids=['within_thirty_minutes_of_the_stamp', 'thirty_minutes_after_the_stamp'],
)
@pytest.mark.usefixtures('f_reservation')
def test_writer_that_read_the_stamp_of_the_slot_waits_for_it_on_that_stamp_as_before(
    f_gate: ZoneWriteGate,
    later: timedelta,
    allowed: bool,
) -> None:
    reservation = f_gate.reserve('kitchen', NOW + later, stamped_at=NOW)

    assert (reservation is not None) is allowed


@pytest.mark.usefixtures('f_reservation')
def test_writer_that_took_its_time_before_the_slot_was_reserved_waits_for_it_on_the_monotonic_clock(
    f_gate: ZoneWriteGate,
) -> None:
    assert f_gate.reserve('kitchen', NOW - timedelta(seconds=5), stamped_at=NOW) is None


@pytest.mark.usefixtures('f_reservation')
def test_writer_reads_a_stamp_later_than_a_clock_set_back_as_before(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    f_wall_clock: WallClockStub,
) -> None:
    f_clock.advance(60)
    f_wall_clock.advance(timedelta(minutes=-59))

    assert f_gate.reserve('kitchen', NOW - timedelta(minutes=59), stamped_at=NOW) == WriteReservation(
        zone='kitchen',
        slot=WriteSlot(at=NOW - timedelta(minutes=59), moment=START + 60),
        previous=WRITTEN,
    )


@pytest.mark.usefixtures('f_reservation')
def test_writer_holds_no_other_zone(f_gate: ZoneWriteGate) -> None:
    assert f_gate.reserve('living-room', NOW, stamped_at=None) == WriteReservation(
        zone='living-room',
        slot=WRITTEN,
        previous=None,
    )


@pytest.mark.usefixtures('f_reservation_overridden_by_a_shed')
def test_override_takes_the_slot_within_thirty_minutes_and_holds_every_later_writer(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
) -> None:
    f_clock.advance(1799)
    held = f_gate.reserve('kitchen', NOW + timedelta(minutes=31), stamped_at=NOW)
    f_clock.advance(1)
    taken = f_gate.reserve('kitchen', NOW + timedelta(minutes=31), stamped_at=NOW)

    assert (held, taken) == (
        None,
        WriteReservation(
            zone='kitchen',
            slot=WriteSlot(at=NOW + timedelta(minutes=31), moment=START + 1810),
            previous=OVERRIDDEN,
        ),
    )


@pytest.mark.parametrize(
    ('seconds', 'allowed'),
    [(1799, False), (1800, True)],
    ids=['within_thirty_minutes_of_the_shed', 'thirty_minutes_after_the_shed'],
)
@pytest.mark.usefixtures('f_reservation_overridden_by_a_shed')
def test_writer_that_read_the_stamp_of_a_shed_waits_thirty_minutes_on_the_monotonic_clock_whatever_the_clock_reads(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    seconds: int,
    allowed: bool,
) -> None:
    f_clock.advance(seconds)

    reservation = f_gate.reserve('kitchen', NOW + timedelta(hours=2), stamped_at=OVERRIDDEN.at)

    assert (reservation is not None) is allowed


def test_cancelled_reservation_hands_the_slot_back_to_the_write_before_it(
    f_gate: ZoneWriteGate,
    f_reservation_half_an_hour_later: WriteReservation,
) -> None:
    f_gate.cancel(f_reservation_half_an_hour_later)

    assert f_gate.reserve('kitchen', NOW + HALF_HOUR, stamped_at=NOW) == WriteReservation(
        zone='kitchen',
        slot=WriteSlot(at=NOW + HALF_HOUR, moment=START + 1800),
        previous=WRITTEN,
    )


def test_cancelled_first_reservation_leaves_the_zone_free(
    f_gate: ZoneWriteGate,
    f_reservation: WriteReservation,
) -> None:
    f_gate.cancel(f_reservation)

    assert f_gate.reserve('kitchen', NOW, stamped_at=None) == WriteReservation(
        zone='kitchen',
        slot=WRITTEN,
        previous=None,
    )


def test_cancel_keeps_a_slot_overridden_since_the_reservation(
    f_gate: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    f_reservation_overridden_by_a_shed: WriteReservation,
) -> None:
    f_gate.cancel(f_reservation_overridden_by_a_shed)
    f_clock.advance(1800)

    assert f_gate.reserve('kitchen', NOW + timedelta(minutes=31), stamped_at=None) == WriteReservation(
        zone='kitchen',
        slot=WriteSlot(at=NOW + timedelta(minutes=31), moment=START + 1810),
        previous=OVERRIDDEN,
    )


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


@pytest.mark.usefixtures('f_reservation')
def test_cleared_zone_takes_its_next_write_at_once(f_gate: ZoneWriteGate) -> None:
    freed = f_gate.clear('kitchen')

    assert (freed, f_gate.reserve('kitchen', NOW + timedelta(minutes=1), stamped_at=None)) == (
        True,
        WriteReservation(
            zone='kitchen',
            slot=WriteSlot(at=NOW + timedelta(minutes=1), moment=START),
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
                    slot=WriteSlot(at=NOW + HALF_HOUR, moment=START + 1810),
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

    assert (freed, f_gate.reserve('kitchen', NOW + HALF_HOUR, stamped_at=None)) == expected
