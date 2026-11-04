import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.services.write_gate import ZoneWriteGate
from apps.core.stubs import MonotonicClockStub
from apps.power.domain.loads import LiveShed, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.stubs import LoadStoreStub
from apps.power.usecases.recover_write_holds import (
    RecoverWriteHoldsInputDTO,
    RecoverWriteHoldsOutputDTO,
    RecoverWriteHoldsUseCase,
)

NOW = datetime(2026, 11, 3, 7, 0, tzinfo=UTC)
START = 50.0
SHED_AT = NOW - timedelta(minutes=5)
REST_OF_THE_HOLD = 1500
HANDED_BACK_KITCHEN = LoadRecord(
    slug='kitchen',
    name='Кухня',
    kind=LoadKind.ZONE,
    zone_slug='kitchen',
    device_slug='thermostat-kitchen',
    priority=5,
    phase=LoadPhase.L2,
    nominal_amps=None,
    learned=None,
    live=True,
    shed=LiveShed(
        at=SHED_AT,
        reason=ShedReason.OVERLOAD,
        phase=LoadPhase.L2,
        was_on=None,
        device='thermostat-kitchen',
        handed_back=True,
    ),
    last_command_at=SHED_AT,
)
LIVING_ROOM_SHED_AN_HOUR_AGO = replace(
    HANDED_BACK_KITCHEN,
    slug='living-room',
    name='Гостиная',
    zone_slug='living-room',
    device_slug='thermostat-living-room',
    priority=7,
    shed=LiveShed(
        at=NOW - timedelta(hours=1),
        reason=ShedReason.OVERLOAD,
        phase=LoadPhase.L2,
        was_on=None,
        device='thermostat-living-room',
    ),
    last_command_at=NOW - timedelta(hours=1),
)
HALLWAY_WRITTEN_HALF_AN_HOUR_AFTER_ITS_SHED = replace(
    HANDED_BACK_KITCHEN,
    slug='hallway',
    name='Прихожая',
    zone_slug='hallway',
    device_slug='thermostat-hallway',
    priority=4,
    shed=LiveShed(
        at=NOW - timedelta(minutes=30),
        reason=ShedReason.OVERLOAD,
        phase=LoadPhase.L2,
        was_on=None,
        device='thermostat-hallway',
        handed_back=True,
    ),
    last_command_at=NOW,
)
SHED_BOILER = replace(
    HANDED_BACK_KITCHEN,
    slug='boiler-1f',
    name='Бойлер 1 этаж',
    kind=LoadKind.PLUG,
    zone_slug=None,
    device_slug='plug-boiler-1f',
    priority=1,
    shed=LiveShed(
        at=SHED_AT,
        reason=ShedReason.OVERLOAD,
        phase=LoadPhase.L2,
        was_on=True,
        device='plug-boiler-1f',
    ),
    nominal_amps=Decimal('6.0'),
    last_command_at=None,
)


def _boost_frees(writes: ZoneWriteGate, zone: str) -> bool:
    with writes.clearing((zone,)) as freed:
        return zone in freed


@pytest.fixture
def f_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=START)


@pytest.fixture
def f_writes(f_clock: MonotonicClockStub) -> ZoneWriteGate:
    return ZoneWriteGate(clock=f_clock, zone_lock=threading.Lock)


@pytest.fixture
def loads() -> LoadStoreStub:
    return LoadStoreStub(
        records=[
            SHED_BOILER,
            HANDED_BACK_KITCHEN,
            LIVING_ROOM_SHED_AN_HOUR_AGO,
            HALLWAY_WRITTEN_HALF_AN_HOUR_AFTER_ITS_SHED,
        ],
        live_zones=frozenset({'kitchen', 'living-room', 'hallway'}),
    )


@pytest.fixture
def f_use_case(loads: LoadStoreStub, f_writes: ZoneWriteGate) -> RecoverWriteHoldsUseCase:
    return RecoverWriteHoldsUseCase(loads=loads, writes=f_writes, dry_run=False)


@pytest.fixture
def f_dry_run_use_case(loads: LoadStoreStub, f_writes: ZoneWriteGate) -> RecoverWriteHoldsUseCase:
    return RecoverWriteHoldsUseCase(loads=loads, writes=f_writes, dry_run=True)


@pytest.fixture
def f_recovered_writes(f_use_case: RecoverWriteHoldsUseCase, f_writes: ZoneWriteGate) -> ZoneWriteGate:
    f_use_case.execute(RecoverWriteHoldsInputDTO(now=NOW))
    return f_writes


@pytest.fixture
def f_writes_recovered_on_a_clock_behind_the_shed(
    f_use_case: RecoverWriteHoldsUseCase,
    f_writes: ZoneWriteGate,
) -> ZoneWriteGate:
    f_use_case.execute(RecoverWriteHoldsInputDTO(now=SHED_AT - timedelta(hours=1)))
    return f_writes


def test_recovery_holds_each_zone_shed_live_less_than_thirty_minutes_before_the_start(
    f_use_case: RecoverWriteHoldsUseCase,
) -> None:
    assert f_use_case.execute(RecoverWriteHoldsInputDTO(now=NOW)) == RecoverWriteHoldsOutputDTO(zones=('kitchen',))


@pytest.mark.parametrize(
    ('seconds', 'freed'),
    [(REST_OF_THE_HOLD - 1, False), (REST_OF_THE_HOLD, True)],
    ids=['within_thirty_minutes_of_the_shed', 'thirty_minutes_after_the_shed'],
)
def test_recovered_hold_keeps_a_boost_from_freeing_the_zone_until_thirty_minutes_after_the_shed(
    f_recovered_writes: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    seconds: int,
    freed: bool,
) -> None:
    f_clock.advance(seconds)

    assert _boost_frees(f_recovered_writes, 'kitchen') is freed


@pytest.mark.parametrize(
    ('seconds', 'written'),
    [(REST_OF_THE_HOLD - 1, False), (REST_OF_THE_HOLD, True)],
    ids=['within_thirty_minutes_of_the_shed', 'thirty_minutes_after_the_shed'],
)
def test_recovered_hold_keeps_the_zone_from_a_write_until_thirty_minutes_after_the_shed_whatever_the_clock_reads(
    f_recovered_writes: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    seconds: int,
    written: bool,
) -> None:
    f_clock.advance(seconds)

    reservation = f_recovered_writes.reserve(
        'kitchen',
        NOW + timedelta(hours=3),
        stamped_at=SHED_AT,
        read_at=f_clock(),
    )

    assert (reservation is not None) is written


@pytest.mark.parametrize(
    ('seconds', 'freed'),
    [(1799, False), (1800, True)],
    ids=['within_thirty_minutes_of_the_start', 'thirty_minutes_after_the_start'],
)
def test_hold_recovered_on_a_clock_reading_earlier_than_the_shed_counts_thirty_minutes_from_the_start(
    f_writes_recovered_on_a_clock_behind_the_shed: ZoneWriteGate,
    f_clock: MonotonicClockStub,
    seconds: int,
    freed: bool,
) -> None:
    f_clock.advance(seconds)

    assert _boost_frees(f_writes_recovered_on_a_clock_behind_the_shed, 'kitchen') is freed


def test_recovery_on_a_clock_reading_earlier_than_the_sheds_holds_no_floor_written_half_an_hour_after_its_shed(
    f_use_case: RecoverWriteHoldsUseCase,
    f_writes: ZoneWriteGate,
) -> None:
    output = f_use_case.execute(RecoverWriteHoldsInputDTO(now=SHED_AT - timedelta(hours=1)))

    assert (output, _boost_frees(f_writes, 'hallway'), _boost_frees(f_writes, 'kitchen')) == (
        RecoverWriteHoldsOutputDTO(zones=('kitchen', 'living-room')),
        True,
        False,
    )


def test_teplo_in_dry_run_recovers_no_hold(
    f_dry_run_use_case: RecoverWriteHoldsUseCase,
    f_writes: ZoneWriteGate,
) -> None:
    output = f_dry_run_use_case.execute(RecoverWriteHoldsInputDTO(now=NOW))

    assert (output, _boost_frees(f_writes, 'kitchen')) == (RecoverWriteHoldsOutputDTO(zones=()), True)
