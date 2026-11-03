import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from django.db import OperationalError

from apps.climate.domain.write_guard import WriteReservation, WriteSlot
from apps.climate.services.write_gate import ZoneWriteGate
from apps.climate.stubs import ContendedLockStub
from apps.core.stubs import MonotonicClockStub

NOW = datetime(2026, 11, 3, 7, 0, tzinfo=UTC)
START = 1000.0
HALF_HOUR = timedelta(minutes=30)
STORED = START + 2
SHED_AT = NOW + timedelta(seconds=10)
WAIT = 5.0


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


@dataclass(frozen=True, kw_only=True)
class ShedDuringWrite:
    reservation: WriteReservation
    command: Callable[[], None]
    sheds: list[Future[None]]


def _never_contended() -> None:
    raise AssertionError('a zone lock was waited for')


@pytest.fixture
def f_sent() -> list[str]:
    return []


@pytest.fixture
def lock_contended() -> threading.Event:
    return threading.Event()


@pytest.fixture
def shed_may_finish() -> threading.Event:
    return threading.Event()


@pytest.fixture
def guard_thread() -> Iterator[ThreadPoolExecutor]:
    with ThreadPoolExecutor(max_workers=1) as executor:
        yield executor


@pytest.fixture
def f_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=START)


@pytest.fixture
def f_gate(f_clock: MonotonicClockStub) -> ZoneWriteGate:
    return ZoneWriteGate(clock=f_clock, zone_lock=threading.Lock)


@pytest.fixture
def f_contended_gate(f_clock: MonotonicClockStub, lock_contended: threading.Event) -> ZoneWriteGate:
    return ZoneWriteGate(clock=f_clock, zone_lock=partial(ContendedLockStub, contended=lock_contended.set))


@pytest.fixture
def f_gate_releasing_the_shed(f_clock: MonotonicClockStub, shed_may_finish: threading.Event) -> ZoneWriteGate:
    return ZoneWriteGate(clock=f_clock, zone_lock=partial(ContendedLockStub, contended=shed_may_finish.set))


@pytest.fixture
def f_uncontended_gate(f_clock: MonotonicClockStub) -> ZoneWriteGate:
    return ZoneWriteGate(clock=f_clock, zone_lock=partial(ContendedLockStub, contended=_never_contended))


@pytest.fixture
def f_shed_during_a_write(
    f_contended_gate: ZoneWriteGate,
    f_sent: list[str],
    lock_contended: threading.Event,
    guard_thread: ThreadPoolExecutor,
) -> ShedDuringWrite:
    reservation = f_contended_gate.reserve('kitchen', NOW, stamped_at=None, read_at=START)
    assert reservation is not None
    sheds: list[Future[None]] = []

    def shed() -> None:
        f_contended_gate.send(f_contended_gate.override('kitchen', SHED_AT), partial(f_sent.append, 'shed'))

    def write_in_flight() -> None:
        sheds.append(guard_thread.submit(shed))
        assert lock_contended.wait(timeout=WAIT)
        f_sent.append('write')

    return ShedDuringWrite(reservation=reservation, command=write_in_flight, sheds=sheds)


@pytest.fixture
def f_write_after_a_shed_in_flight(
    f_gate_releasing_the_shed: ZoneWriteGate,
    f_sent: list[str],
    shed_may_finish: threading.Event,
    guard_thread: ThreadPoolExecutor,
) -> ShedDuringWrite:
    reservation = f_gate_releasing_the_shed.reserve('kitchen', NOW, stamped_at=None, read_at=START)
    assert reservation is not None
    with f_gate_releasing_the_shed.stamping(reservation):
        pass
    shed_started = threading.Event()

    def shed_in_flight() -> None:
        shed_started.set()
        assert shed_may_finish.wait(timeout=WAIT)
        f_sent.append('shed')

    def shed() -> None:
        f_gate_releasing_the_shed.send(f_gate_releasing_the_shed.override('kitchen', SHED_AT), shed_in_flight)

    sheds = [guard_thread.submit(shed)]
    assert shed_started.wait(timeout=WAIT)
    return ShedDuringWrite(reservation=reservation, command=partial(f_sent.append, 'write'), sheds=sheds)


@pytest.fixture
def f_kitchen_reserved_on_an_uncontended_gate(f_uncontended_gate: ZoneWriteGate) -> WriteReservation:
    reservation = f_uncontended_gate.reserve('kitchen', NOW, stamped_at=None, read_at=START)
    assert reservation is not None
    return reservation


@pytest.fixture
def f_living_room_shed(f_uncontended_gate: ZoneWriteGate, f_sent: list[str]) -> Callable[[], None]:
    shed = f_uncontended_gate.override('living-room', SHED_AT)
    return partial(f_uncontended_gate.send, shed, partial(f_sent.append, 'living-room shed'))


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
    [
        (_living_room_written, (True, ['kitchen'])),
        (_kitchen_shed, (False, [])),
        (_kitchen_boosted, (True, ['kitchen'])),
    ],
    ids=['another_zone_written', 'a_shed_took_the_slot', 'a_boost_freed_the_slot'],
    indirect=['f_reservation_after_another_write'],
)
def test_write_goes_out_until_another_write_takes_the_slot(
    f_gate: ZoneWriteGate,
    f_reservation_after_another_write: WriteReservation,
    f_sent: list[str],
    expected: tuple[bool, list[str]],
) -> None:
    written = f_gate.write(f_reservation_after_another_write, partial(f_sent.append, 'kitchen'))

    assert (written, f_sent) == expected


def test_write_whose_stamp_is_stored_still_goes_out(
    f_gate: ZoneWriteGate,
    f_stored_reservation: WriteReservation,
    f_sent: list[str],
) -> None:
    written = f_gate.write(f_stored_reservation, partial(f_sent.append, 'kitchen'))

    assert (written, f_sent) == (True, ['kitchen'])


def test_shed_that_takes_the_slot_during_a_write_in_flight_goes_out_once_the_write_is_done(
    f_contended_gate: ZoneWriteGate,
    f_shed_during_a_write: ShedDuringWrite,
    f_sent: list[str],
) -> None:
    written = f_contended_gate.write(f_shed_during_a_write.reservation, f_shed_during_a_write.command)
    f_shed_during_a_write.sheds[0].result(timeout=WAIT)

    assert (written, f_sent) == (True, ['write', 'shed'])


def test_write_that_reaches_its_zone_while_a_shed_is_in_flight_sends_nothing_after_it(
    f_gate_releasing_the_shed: ZoneWriteGate,
    f_write_after_a_shed_in_flight: ShedDuringWrite,
    f_sent: list[str],
) -> None:
    written = f_gate_releasing_the_shed.write(
        f_write_after_a_shed_in_flight.reservation,
        f_write_after_a_shed_in_flight.command,
    )
    f_write_after_a_shed_in_flight.sheds[0].result(timeout=WAIT)

    assert (written, f_sent) == (False, ['shed'])


def test_shed_while_a_stamp_of_its_zone_is_stored_goes_out_at_once_and_holds_the_write(
    f_uncontended_gate: ZoneWriteGate,
    f_kitchen_reserved_on_an_uncontended_gate: WriteReservation,
    f_sent: list[str],
) -> None:
    with f_uncontended_gate.stamping(f_kitchen_reserved_on_an_uncontended_gate):
        f_uncontended_gate.send(f_uncontended_gate.override('kitchen', SHED_AT), partial(f_sent.append, 'shed'))
    written = f_uncontended_gate.write(f_kitchen_reserved_on_an_uncontended_gate, partial(f_sent.append, 'write'))

    assert (written, f_sent) == (False, ['shed'])


def test_shed_of_another_zone_goes_out_at_once_during_a_write(
    f_uncontended_gate: ZoneWriteGate,
    f_kitchen_reserved_on_an_uncontended_gate: WriteReservation,
    f_living_room_shed: Callable[[], None],
    f_sent: list[str],
) -> None:
    written = f_uncontended_gate.write(f_kitchen_reserved_on_an_uncontended_gate, f_living_room_shed)

    assert (written, f_sent) == (True, ['living-room shed'])


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
