import threading
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import OperationalError
from structlog.testing import capture_logs

from apps.climate.domain.events import SourceManualChange
from apps.climate.domain.heat_sources import HeatSourceKind, SourceSettings
from apps.climate.services.write_gate import KeyedWriteGate
from apps.climate.stubs import HeatSourceStoreStub, SourceHoldStoreStub
from apps.climate.usecases.watch_source_changes import (
    WatchSourceChangesInputDTO,
    WatchSourceChangesOutputDTO,
    WatchSourceChangesUseCase,
)
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.readings import ClimateMode, ClimateUnitState, ReadingPair

MOSCOW = ZoneInfo('Europe/Moscow')
NOW = datetime(2026, 11, 4, 16, 40, tzinfo=MOSCOW)
HOLD_ENDS = NOW + timedelta(hours=2)
FIRST_POLL = 1000.0
SECOND_POLL = FIRST_POLL + 30
INTERVAL = timedelta(minutes=5)
WATCHED_CONVECTOR = SourceSettings(
    slug='convector-office',
    name='Конвектор: кабинет',
    room_slug='office',
    room_name='Кабинет',
    kind=HeatSourceKind.HEATER,
    device_slug='heater-office',
    priority=1,
    live=False,
    setpoint_bias=Decimal('0.0'),
    max_setpoint=Decimal('24.0'),
    min_outdoor=None,
    write_interval=INTERVAL,
    rated_watts=2000,
    manual_until=None,
    last_command_at=None,
    last_dry_run_at=None,
    commanded_power=None,
    commanded_setpoint=None,
    load='convector-office',
    load_live=False,
    load_management_live=False,
)
LIVE_CONVECTOR = replace(WATCHED_CONVECTOR, live=True, load_live=True, load_management_live=True)
AT_22 = ClimateUnitState(
    mode=ClimateMode.HEAT,
    target=Decimal(22),
    room_temperature=Decimal(21),
    read_at=NOW,
    level='High',
)


def _changed(after: ClimateUnitState) -> WatchSourceChangesInputDTO:
    pair = ReadingPair(
        slug='heater-office',
        before=AT_22,
        after=after,
        before_moment=FIRST_POLL,
        after_moment=SECOND_POLL,
    )
    return WatchSourceChangesInputDTO(pairs=(pair,), now=NOW)


def _manual_change(*, dry_run: bool) -> SourceManualChange:
    return SourceManualChange(
        source='convector-office',
        name='Конвектор: кабинет',
        power=None,
        setpoint=(Decimal(22), Decimal(25)),
        until=HOLD_ENDS,
        dry_run=dry_run,
        occurred_at=NOW,
    )


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=SECOND_POLL)


@pytest.fixture
def writes(clock: MonotonicClockStub) -> KeyedWriteGate:
    return KeyedWriteGate(clock=clock, key_lock=threading.Lock)


@pytest.fixture
def written_by_teplo(writes: KeyedWriteGate) -> KeyedWriteGate:
    writes.reserve('convector-office', NOW, stamped_at=None, read_at=FIRST_POLL, interval=INTERVAL)
    return writes


@pytest.fixture
def written_after_the_second_poll(clock: MonotonicClockStub, writes: KeyedWriteGate) -> KeyedWriteGate:
    clock.advance(5)
    writes.reserve('convector-office', NOW, stamped_at=None, read_at=SECOND_POLL + 5, interval=INTERVAL)
    return writes


@pytest.fixture
def f_holds(request: pytest.FixtureRequest) -> SourceHoldStoreStub:
    return SourceHoldStoreStub(errors=getattr(request, 'param', {}))


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


def _use_case(
    source: SourceSettings,
    holds: SourceHoldStoreStub,
    writes: KeyedWriteGate,
    events: list[DomainEvent],
    clock: MonotonicClockStub,
    *,
    dry_run: bool = False,
) -> WatchSourceChangesUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(SourceManualChange, events.append)
    return WatchSourceChangesUseCase(
        sources=HeatSourceStoreStub(sources=[source], errors={}),
        holds=holds,
        writes=writes,
        clock=clock,
        dry_run=dry_run,
        dispatcher=dispatcher,
    )


@pytest.fixture
def f_live_use_case(
    f_holds: SourceHoldStoreStub,
    writes: KeyedWriteGate,
    f_events: list[DomainEvent],
    clock: MonotonicClockStub,
) -> WatchSourceChangesUseCase:
    return _use_case(LIVE_CONVECTOR, f_holds, writes, f_events, clock)


@pytest.fixture
def f_watching_use_case(
    f_holds: SourceHoldStoreStub,
    writes: KeyedWriteGate,
    f_events: list[DomainEvent],
    clock: MonotonicClockStub,
) -> WatchSourceChangesUseCase:
    return _use_case(WATCHED_CONVECTOR, f_holds, writes, f_events, clock)


@pytest.fixture
def f_dry_run_use_case(
    f_holds: SourceHoldStoreStub,
    writes: KeyedWriteGate,
    f_events: list[DomainEvent],
    clock: MonotonicClockStub,
) -> WatchSourceChangesUseCase:
    return _use_case(LIVE_CONVECTOR, f_holds, writes, f_events, clock, dry_run=True)


@pytest.fixture
def f_use_case_after_a_write(
    f_holds: SourceHoldStoreStub,
    written_by_teplo: KeyedWriteGate,
    f_events: list[DomainEvent],
    clock: MonotonicClockStub,
) -> WatchSourceChangesUseCase:
    return _use_case(LIVE_CONVECTOR, f_holds, written_by_teplo, f_events, clock)


@pytest.fixture
def f_use_case_after_a_write_since_the_second_poll(
    f_holds: SourceHoldStoreStub,
    written_after_the_second_poll: KeyedWriteGate,
    f_events: list[DomainEvent],
    clock: MonotonicClockStub,
) -> WatchSourceChangesUseCase:
    return _use_case(LIVE_CONVECTOR, f_holds, written_after_the_second_poll, f_events, clock)


@pytest.fixture
def f_overtaken_use_case(
    writes: KeyedWriteGate,
    f_events: list[DomainEvent],
    clock: MonotonicClockStub,
) -> WatchSourceChangesUseCase:
    holds = SourceHoldStoreStub(errors={}, stamps={'convector-office': NOW})
    return _use_case(LIVE_CONVECTOR, holds, writes, f_events, clock)


def test_setpoint_changed_on_a_live_source_holds_it_for_two_hours(
    f_live_use_case: WatchSourceChangesUseCase,
    f_holds: SourceHoldStoreStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_live_use_case.execute(_changed(replace(AT_22, target=Decimal(25))))

    assert (output, f_holds.holds, f_events) == (
        WatchSourceChangesOutputDTO(held=('convector-office',), journaled=('convector-office',)),
        [('convector-office', HOLD_ENDS)],
        [_manual_change(dry_run=False)],
    )


def test_source_switched_off_by_hand_is_held_too(
    f_live_use_case: WatchSourceChangesUseCase,
    f_events: list[DomainEvent],
) -> None:
    f_live_use_case.execute(_changed(replace(AT_22, mode=ClimateMode.OFF)))

    assert f_events == [replace(_manual_change(dry_run=False), power=(True, False), setpoint=None)]


@pytest.mark.parametrize(
    'use_case',
    ['f_watching_use_case', 'f_dry_run_use_case'],
    ids=['source_watching', 'teplo_in_dry_run'],
)
def test_change_on_a_source_whose_commands_do_not_go_out_is_only_journaled(
    use_case: str,
    request: pytest.FixtureRequest,
    f_holds: SourceHoldStoreStub,
    f_events: list[DomainEvent],
) -> None:
    output = request.getfixturevalue(use_case).execute(_changed(replace(AT_22, target=Decimal(25))))

    assert (output, f_holds.holds, f_events) == (
        WatchSourceChangesOutputDTO(held=(), journaled=('convector-office',)),
        [],
        [_manual_change(dry_run=True)],
    )


def test_level_changed_on_a_source_is_no_manual_change(
    f_live_use_case: WatchSourceChangesUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_live_use_case.execute(_changed(replace(AT_22, level='Low')))

    assert (output, f_events) == (WatchSourceChangesOutputDTO(held=(), journaled=()), [])


@pytest.mark.parametrize(
    'use_case',
    ['f_use_case_after_a_write', 'f_use_case_after_a_write_since_the_second_poll'],
    ids=['written_before_the_second_poll', 'written_after_the_second_poll_before_the_watch_read_the_sources'],
)
def test_change_that_follows_a_write_of_teplo_is_not_taken_for_the_owner(
    use_case: str,
    request: pytest.FixtureRequest,
    f_holds: SourceHoldStoreStub,
    f_events: list[DomainEvent],
) -> None:
    output = request.getfixturevalue(use_case).execute(_changed(replace(AT_22, target=Decimal(19))))

    assert (output, f_holds.holds, f_events) == (WatchSourceChangesOutputDTO(held=(), journaled=()), [], [])


def test_change_on_a_source_teplo_stamped_since_the_watch_read_it_is_neither_held_nor_journaled(
    f_overtaken_use_case: WatchSourceChangesUseCase,
    f_events: list[DomainEvent],
) -> None:
    with capture_logs() as logs:
        output = f_overtaken_use_case.execute(_changed(replace(AT_22, target=Decimal(25))))

    assert (output, f_events, [log for log in logs if log['event'] == 'source_manual_hold_overtaken']) == (
        WatchSourceChangesOutputDTO(held=(), journaled=()),
        [],
        [{'event': 'source_manual_hold_overtaken', 'log_level': 'info', 'source': 'convector-office'}],
    )


@pytest.mark.parametrize('f_holds', [{'convector-office': OperationalError('database is locked')}], indirect=True)
def test_hold_the_database_refuses_is_neither_held_nor_journaled(
    f_live_use_case: WatchSourceChangesUseCase,
    f_holds: SourceHoldStoreStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_live_use_case.execute(_changed(replace(AT_22, target=Decimal(25))))

    assert (output, f_holds.holds, f_events) == (WatchSourceChangesOutputDTO(held=(), journaled=()), [], [])
