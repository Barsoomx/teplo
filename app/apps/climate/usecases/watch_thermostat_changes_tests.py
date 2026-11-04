import threading
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import OperationalError
from structlog.testing import capture_logs

from apps.climate.domain.control import ClimateGates
from apps.climate.domain.events import ZoneManualChange
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone import AirTargets, FloorSetpoints, ZoneSettings
from apps.climate.services.write_gate import KeyedWriteGate
from apps.climate.stubs import ClimateGatesStoreStub, ZoneHoldStoreStub, ZoneStoreStub
from apps.climate.usecases.watch_thermostat_changes import (
    WatchThermostatChangesInputDTO,
    WatchThermostatChangesOutputDTO,
    WatchThermostatChangesUseCase,
)
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.readings import ReadingPair, ThermostatMode, ThermostatState

MOSCOW = ZoneInfo('Europe/Moscow')
NOW = datetime(2026, 11, 4, 16, 40, tzinfo=MOSCOW)
HOLD_ENDS = NOW + timedelta(hours=2)
FIRST_POLL = 1000.0
SECOND_POLL = FIRST_POLL + 30
LOCKED = 'database is locked'
KITCHEN = ZoneSettings(
    slug='kitchen',
    name='Кухня',
    kind=ZoneKind.AIR,
    thermostat_slug='thermostat-kitchen',
    air_sensor_slug='air-kitchen',
    heating_enabled=True,
    live_control=True,
    air_control_live=False,
    setpoints=FloorSetpoints(
        comfort=Decimal('28.0'),
        night=Decimal('22.0'),
        boost=Decimal('30.0'),
        idle=Decimal('18.0'),
        limit=Decimal('30.0'),
    ),
    air_targets=AirTargets(comfort=Decimal('22.0'), night=Decimal('19.5')),
    preheat_lead=timedelta(hours=3),
    windows=(),
    boost_until=None,
    last_command_at=None,
    last_dry_run_at=None,
    last_air_dry_run_at=None,
    room_slug='open-space',
)
HALLWAY = replace(KITCHEN, slug='hallway', name='Прихожая', thermostat_slug='thermostat-hallway')
COMFORT = ThermostatState(
    power=True,
    mode=ThermostatMode.HOLD,
    setpoint=Decimal('28.0'),
    floor_temperature=Decimal('24.5'),
    heating=True,
    read_at=NOW,
)
LOWERED = replace(COMFORT, setpoint=Decimal('25.0'))


def _pair(slug: str, after: ThermostatState = LOWERED) -> ReadingPair[ThermostatState]:
    return ReadingPair(slug=slug, before=COMFORT, after=after, before_moment=FIRST_POLL, after_moment=SECOND_POLL)


def _changed(slug: str = 'thermostat-kitchen', now: datetime = NOW) -> WatchThermostatChangesInputDTO:
    return WatchThermostatChangesInputDTO(pairs=(_pair(slug),), now=now)


def _manual_change(*, until: datetime = HOLD_ENDS, dry_run: bool, at: datetime = NOW) -> ZoneManualChange:
    return ZoneManualChange(
        zone='kitchen',
        name='Кухня',
        power=None,
        mode=None,
        setpoint=(Decimal('28.0'), Decimal('25.0')),
        until=until,
        dry_run=dry_run,
        occurred_at=at,
    )


def _use_case(
    holds: ZoneHoldStoreStub,
    writes: KeyedWriteGate,
    events: list[DomainEvent],
    *,
    live: bool,
) -> WatchThermostatChangesUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(ZoneManualChange, events.append)
    return WatchThermostatChangesUseCase(
        zones=ZoneStoreStub(zones=[KITCHEN, HALLWAY], errors={}),
        holds=holds,
        gates=ClimateGatesStoreStub(gates=ClimateGates(manual_hold_live=live)),
        writes=writes,
        dispatcher=dispatcher,
    )


def _written(clock: MonotonicClockStub, moment: float) -> KeyedWriteGate:
    writes = KeyedWriteGate(clock=clock, key_lock=threading.Lock)
    clock.advance(moment - clock())
    writes.reserve('kitchen', NOW, stamped_at=None, read_at=moment)
    clock.advance(SECOND_POLL - moment)
    return writes


@pytest.fixture
def f_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=SECOND_POLL)


@pytest.fixture
def f_writes(f_clock: MonotonicClockStub) -> KeyedWriteGate:
    return KeyedWriteGate(clock=f_clock, key_lock=threading.Lock)


@pytest.fixture
def f_holds() -> ZoneHoldStoreStub:
    return ZoneHoldStoreStub(errors={})


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_use_case(
    f_holds: ZoneHoldStoreStub,
    f_writes: KeyedWriteGate,
    f_events: list[DomainEvent],
) -> WatchThermostatChangesUseCase:
    return _use_case(f_holds, f_writes, f_events, live=True)


@pytest.fixture
def f_watching_use_case(
    f_holds: ZoneHoldStoreStub,
    f_writes: KeyedWriteGate,
    f_events: list[DomainEvent],
) -> WatchThermostatChangesUseCase:
    return _use_case(f_holds, f_writes, f_events, live=False)


@pytest.fixture
def f_use_case_after_a_write(
    request: pytest.FixtureRequest,
    f_holds: ZoneHoldStoreStub,
    f_events: list[DomainEvent],
) -> WatchThermostatChangesUseCase:
    clock = MonotonicClockStub(now=FIRST_POLL - 120)
    return _use_case(f_holds, _written(clock, request.param), f_events, live=True)


@pytest.fixture
def f_held_use_case(f_use_case: WatchThermostatChangesUseCase) -> WatchThermostatChangesUseCase:
    f_use_case.execute(_changed())
    return f_use_case


@pytest.fixture
def f_locked_use_case(f_writes: KeyedWriteGate, f_events: list[DomainEvent]) -> WatchThermostatChangesUseCase:
    holds = ZoneHoldStoreStub(errors={'kitchen': OperationalError(LOCKED)})
    return _use_case(holds, f_writes, f_events, live=True)


def test_change_made_on_a_thermostat_holds_its_zone_for_two_hours_and_is_journaled(
    f_use_case: WatchThermostatChangesUseCase,
    f_holds: ZoneHoldStoreStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(_changed())

    assert (output, f_holds.holds, f_events) == (
        WatchThermostatChangesOutputDTO(held=('kitchen',), journaled=('kitchen',)),
        [('kitchen', HOLD_ENDS)],
        [_manual_change(dry_run=False)],
    )


@pytest.mark.parametrize(
    'f_use_case_after_a_write',
    [FIRST_POLL - 60, FIRST_POLL - 30, FIRST_POLL + 10, SECOND_POLL],
    ids=[
        'slot_taken_a_minute_before_the_first_poll',
        'slot_taken_before_the_first_poll',
        'slot_taken_between_the_polls',
        'slot_taken_as_the_second_poll_read',
    ],
    indirect=True,
)
def test_change_teplo_wrote_itself_is_not_taken_for_the_owner(
    f_use_case_after_a_write: WatchThermostatChangesUseCase,
    f_holds: ZoneHoldStoreStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case_after_a_write.execute(_changed())

    assert (output, f_holds.holds, f_events) == (WatchThermostatChangesOutputDTO(held=(), journaled=()), [], [])


@pytest.mark.parametrize('f_use_case_after_a_write', [FIRST_POLL - 61], indirect=True)
def test_change_after_an_earlier_write_of_teplo_holds_its_zone(
    f_use_case_after_a_write: WatchThermostatChangesUseCase,
    f_holds: ZoneHoldStoreStub,
) -> None:
    f_use_case_after_a_write.execute(_changed())

    assert f_holds.holds == [('kitchen', HOLD_ENDS)]


def test_change_while_the_gate_is_off_is_journaled_with_the_would_be_end_and_stores_nothing(
    f_watching_use_case: WatchThermostatChangesUseCase,
    f_holds: ZoneHoldStoreStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_watching_use_case.execute(_changed())

    assert (output, f_holds.holds, f_events) == (
        WatchThermostatChangesOutputDTO(held=(), journaled=('kitchen',)),
        [],
        [_manual_change(dry_run=True)],
    )


def test_change_seen_again_during_a_hold_extends_it(
    f_held_use_case: WatchThermostatChangesUseCase,
    f_holds: ZoneHoldStoreStub,
) -> None:
    f_held_use_case.execute(_changed(now=NOW + timedelta(minutes=30)))

    assert f_holds.holds == [('kitchen', HOLD_ENDS), ('kitchen', HOLD_ENDS + timedelta(minutes=30))]


@pytest.mark.parametrize(
    'pair',
    [_pair('thermostat-attic'), _pair('thermostat-kitchen', after=replace(COMFORT, heating=False))],
    ids=['thermostat_of_no_zone', 'only_the_relay_turned'],
)
def test_reading_that_is_no_change_of_a_zone_holds_nothing(
    f_use_case: WatchThermostatChangesUseCase,
    f_holds: ZoneHoldStoreStub,
    f_events: list[DomainEvent],
    pair: ReadingPair[ThermostatState],
) -> None:
    output = f_use_case.execute(WatchThermostatChangesInputDTO(pairs=(pair,), now=NOW))

    assert (output, f_holds.holds, f_events) == (WatchThermostatChangesOutputDTO(held=(), journaled=()), [], [])


def test_hold_the_database_refuses_is_logged_and_journals_nothing_while_other_zones_are_held(
    f_locked_use_case: WatchThermostatChangesUseCase,
    f_events: list[DomainEvent],
) -> None:
    pairs = (_pair('thermostat-kitchen'), _pair('thermostat-hallway'))

    with capture_logs() as logs:
        output = f_locked_use_case.execute(WatchThermostatChangesInputDTO(pairs=pairs, now=NOW))

    assert (
        output,
        [event.zone for event in f_events if isinstance(event, ZoneManualChange)],
        [log for log in logs if log['event'] == 'zone_manual_hold_failed'],
    ) == (
        WatchThermostatChangesOutputDTO(held=('hallway',), journaled=('hallway',)),
        ['hallway'],
        [{'event': 'zone_manual_hold_failed', 'log_level': 'warning', 'zone': 'kitchen', 'error': LOCKED}],
    )
