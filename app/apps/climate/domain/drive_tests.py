from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.drive import (
    PendingKind,
    PendingWrite,
    StartAnswer,
    StartPermits,
    ZoneDrive,
    pending_write,
    plan_drive,
)
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.modes import ZoneCycleOutcome
from apps.devices.domain.readings import ThermostatMode, ThermostatState

NOW = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)
HOLD_ENDS = NOW + timedelta(hours=1, minutes=40)
COMFORT = ThermostatTarget(power=True, setpoint=Decimal('28.0'))
NIGHT = ThermostatTarget(power=True, setpoint=Decimal('22.0'))
OFF = ThermostatTarget(power=False, setpoint=None)
IDLE = ThermostatState(
    power=True,
    mode=ThermostatMode.HOLD,
    setpoint=Decimal('22.0'),
    floor_temperature=Decimal('22.5'),
    heating=False,
    read_at=NOW,
)
HEATING = replace(IDLE, setpoint=Decimal('25.0'), heating=True)
SWITCHED_OFF = replace(IDLE, power=False)
PERMITS = StartPermits(
    answers={
        'kitchen': StartAnswer(granted=True, note=None),
        'hallway': StartAnswer(granted=False, note=None),
    },
    gated=frozenset({'kitchen', 'hallway', 'living-room'}),
)


def _settled(outcome: ZoneCycleOutcome) -> ZoneDrive:
    return ZoneDrive(change=ThermostatChange(), outcome=outcome, starts=False)


def _due(change: ThermostatChange, *, starts: bool) -> ZoneDrive:
    return ZoneDrive(change=change, outcome=None, starts=starts)


@pytest.mark.parametrize(
    ('state', 'held', 'last_write_at', 'slot_free', 'expected'),
    [
        (IDLE, True, None, True, _settled(ZoneCycleOutcome.SHED)),
        (None, True, None, True, _settled(ZoneCycleOutcome.SHED)),
        (None, False, None, True, _settled(ZoneCycleOutcome.DEVICE_UNAVAILABLE)),
        (replace(IDLE, setpoint=Decimal('28.0')), False, None, True, _settled(ZoneCycleOutcome.UNCHANGED)),
        (IDLE, False, NOW - timedelta(minutes=29), True, _settled(ZoneCycleOutcome.THROTTLED)),
        (
            IDLE,
            False,
            NOW - timedelta(minutes=30),
            True,
            _due(ThermostatChange(setpoint=Decimal('28.0')), starts=True),
        ),
        (IDLE, False, NOW - timedelta(minutes=30), False, _settled(ZoneCycleOutcome.THROTTLED)),
        (replace(IDLE, setpoint=Decimal('28.0')), False, None, False, _settled(ZoneCycleOutcome.UNCHANGED)),
    ],
    ids=[
        'shed_zone_is_held',
        'shed_zone_is_held_whatever_its_thermostat_reads',
        'unavailable_thermostat',
        'thermostat_already_on_target',
        'within_thirty_minutes_of_the_last_write',
        'thirty_minutes_after_the_last_write',
        'write_the_write_gate_holds_back',
        'thermostat_on_target_while_the_write_gate_holds_its_slot',
    ],
)
def test_drive_writes_only_an_allowed_change_to_a_zone_that_is_not_shed(
    state: ThermostatState | None,
    held: bool,
    last_write_at: datetime | None,
    slot_free: bool,
    expected: ZoneDrive,
) -> None:
    drive = plan_drive(
        COMFORT,
        state,
        last_write_at=last_write_at,
        now=NOW,
        held=held,
        hands_off=False,
        slot_free=slot_free,
    )

    assert drive == expected


@pytest.mark.parametrize(
    ('target', 'state', 'expected'),
    [
        (NIGHT, SWITCHED_OFF, _due(ThermostatChange(power=True), starts=True)),
        (COMFORT, IDLE, _due(ThermostatChange(setpoint=Decimal('28.0')), starts=True)),
        (
            COMFORT,
            replace(IDLE, heating=None),
            _due(ThermostatChange(setpoint=Decimal('28.0')), starts=True),
        ),
        (COMFORT, HEATING, _due(ThermostatChange(setpoint=Decimal('28.0')), starts=False)),
        (
            COMFORT,
            replace(HEATING, heating=None),
            _due(ThermostatChange(setpoint=Decimal('28.0')), starts=False),
        ),
        (
            ThermostatTarget(power=True, setpoint=Decimal('22.5')),
            IDLE,
            _due(ThermostatChange(setpoint=Decimal('22.5')), starts=False),
        ),
        (NIGHT, HEATING, _due(ThermostatChange(setpoint=Decimal('22.0')), starts=False)),
        (OFF, IDLE, _due(ThermostatChange(power=False), starts=False)),
        (
            NIGHT,
            replace(IDLE, mode=ThermostatMode.SMART),
            _due(ThermostatChange(mode=ThermostatMode.HOLD), starts=False),
        ),
    ],
    ids=[
        'switching_a_thermostat_on',
        'raising_an_idle_thermostat_above_its_floor',
        'raising_a_thermostat_of_unknown_relay_set_below_its_floor',
        'raising_a_heating_thermostat',
        'raising_a_thermostat_of_unknown_relay_set_above_its_floor',
        'raising_an_idle_thermostat_only_to_its_floor',
        'lowering_the_setpoint',
        'switching_off',
        'switching_to_hold_only',
    ],
)
def test_write_starts_a_zone_that_draws_nothing_and_would_heat(
    target: ThermostatTarget,
    state: ThermostatState,
    expected: ZoneDrive,
) -> None:
    drive = plan_drive(target, state, last_write_at=None, now=NOW, held=False, hands_off=False, slot_free=True)

    assert drive == expected


@pytest.mark.parametrize(
    ('zone', 'writes_live', 'expected'),
    [
        ('kitchen', True, True),
        ('hallway', True, False),
        ('living-room', True, False),
        ('living-room', False, True),
        ('bathroom-1f', True, True),
    ],
    ids=[
        'start_granted_when_asked',
        'start_refused_when_asked',
        'gated_start_it_did_not_ask_for',
        'start_it_did_not_ask_for_whose_writes_are_held_back',
        'start_load_management_does_not_gate',
    ],
)
def test_start_goes_with_the_answer_it_got_and_without_one_only_when_no_live_permit_gates_it(
    zone: str,
    writes_live: bool,
    expected: bool,
) -> None:
    assert PERMITS.granted(zone, writes_live=writes_live) is expected


@pytest.mark.parametrize(
    ('target', 'state', 'last_write_at', 'held', 'writes_live', 'expected'),
    [
        (COMFORT, IDLE, None, True, True, PendingWrite(kind=PendingKind.HELD, at=None)),
        (COMFORT, None, None, False, True, PendingWrite(kind=PendingKind.UNREACHED, at=None)),
        (NIGHT, IDLE, None, False, True, PendingWrite(kind=PendingKind.HOLDS, at=None)),
        (COMFORT, IDLE, None, False, False, PendingWrite(kind=PendingKind.WATCHING, at=None)),
        (COMFORT, IDLE, None, False, True, PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW)),
        (
            COMFORT,
            IDLE,
            NOW - timedelta(minutes=10),
            False,
            True,
            PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW + timedelta(minutes=20)),
        ),
        (
            COMFORT,
            IDLE,
            NOW - timedelta(minutes=45),
            False,
            True,
            PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW),
        ),
        (OFF, SWITCHED_OFF, NOW - timedelta(minutes=10), True, True, PendingWrite(kind=PendingKind.HELD, at=None)),
    ],
    ids=[
        'shed_zone_is_held',
        'thermostat_unread',
        'thermostat_already_holds_its_target',
        'writes_held_back',
        'write_now',
        'write_once_thirty_minutes_have_passed',
        'write_now_long_after_the_last',
        'shed_zone_is_held_whatever_it_reads',
    ],
)
def test_pending_write_names_when_the_target_reaches_the_thermostat(
    target: ThermostatTarget,
    state: ThermostatState | None,
    last_write_at: datetime | None,
    held: bool,
    writes_live: bool,
    expected: PendingWrite,
) -> None:
    pending = pending_write(
        target,
        state,
        last_write_at=last_write_at,
        now=NOW,
        held=held,
        writes_live=writes_live,
        hands_off_until=None,
    )

    assert pending == expected


@pytest.mark.parametrize(
    ('state', 'held', 'hands_off', 'expected'),
    [
        (IDLE, True, True, _settled(ZoneCycleOutcome.SHED)),
        (IDLE, False, True, _settled(ZoneCycleOutcome.HANDS_OFF)),
        (None, False, True, _settled(ZoneCycleOutcome.HANDS_OFF)),
        (replace(IDLE, setpoint=Decimal('28.0')), False, True, _settled(ZoneCycleOutcome.HANDS_OFF)),
    ],
    ids=[
        'shed_wins_over_a_hold_by_hand',
        'held_by_hand',
        'held_by_hand_whatever_its_thermostat_reads',
        'held_by_hand_on_its_target',
    ],
)
def test_drive_leaves_a_zone_held_by_hand_alone_unless_it_is_shed(
    state: ThermostatState | None,
    held: bool,
    hands_off: bool,
    expected: ZoneDrive,
) -> None:
    drive = plan_drive(
        COMFORT,
        state,
        last_write_at=None,
        now=NOW,
        held=held,
        hands_off=hands_off,
        slot_free=True,
    )

    assert drive == expected


@pytest.mark.parametrize(
    ('state', 'held', 'expected'),
    [
        (IDLE, False, PendingWrite(kind=PendingKind.HANDS_OFF, at=HOLD_ENDS)),
        (None, False, PendingWrite(kind=PendingKind.HANDS_OFF, at=HOLD_ENDS)),
        (IDLE, True, PendingWrite(kind=PendingKind.HELD, at=None)),
    ],
    ids=['held_by_hand', 'held_by_hand_whatever_it_reads', 'shed_wins_over_a_hold_by_hand'],
)
def test_pending_write_names_the_end_of_a_hold_by_hand(
    state: ThermostatState | None,
    held: bool,
    expected: PendingWrite,
) -> None:
    pending = pending_write(
        COMFORT,
        state,
        last_write_at=None,
        now=NOW,
        held=held,
        writes_live=True,
        hands_off_until=HOLD_ENDS,
    )

    assert pending == expected
