from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.drive import PendingKind, PendingWrite
from apps.climate.domain.fast_source import (
    SourceChange,
    SourceDrive,
    SourceTarget,
    decide_source_power,
    pending_source_write,
    plan_source_change,
    plan_source_drive,
    room_targets,
    source_setpoint,
    source_starts,
)
from apps.climate.domain.heat_sources import HeatSourceKind, SourceSettings
from apps.climate.domain.modes import ZoneCycleOutcome
from apps.devices.domain.readings import ClimateMode, ClimateUnitState

NOW = datetime(2026, 11, 4, 13, 0, tzinfo=UTC)
INTERVAL = timedelta(minutes=5)
CONVECTOR = SourceSettings(
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
AIR_CONDITIONER = replace(
    CONVECTOR,
    slug='ac-office',
    name='Кондиционер: кабинет',
    kind=HeatSourceKind.AIR_CONDITIONER,
    device_slug='ac-office',
    priority=2,
    min_outdoor=Decimal('-15.0'),
)
HEATING = ClimateUnitState(
    mode=ClimateMode.HEAT,
    target=Decimal(22),
    room_temperature=Decimal(20),
    read_at=NOW,
    level='High',
)
OFF = replace(HEATING, mode=ClimateMode.OFF)
COOLING = replace(HEATING, mode=ClimateMode.COOL)
AT_ITS_SETPOINT = replace(HEATING, room_temperature=Decimal(22))
ON_AT_22 = SourceTarget(power=True, setpoint=Decimal(22))
SWITCHED_OFF = SourceTarget(power=False, setpoint=None)


@pytest.mark.parametrize(
    ('source', 'target', 'setpoint'),
    [
        (CONVECTOR, Decimal('21.5'), Decimal(22)),
        (CONVECTOR, Decimal('21.4'), Decimal(21)),
        (CONVECTOR, Decimal('19.0'), Decimal(19)),
        (replace(CONVECTOR, setpoint_bias=Decimal('1.0')), Decimal('21.5'), Decimal(23)),
        (replace(CONVECTOR, setpoint_bias=Decimal('-0.5')), Decimal('21.5'), Decimal(21)),
        (CONVECTOR, Decimal('26.0'), Decimal(24)),
        (replace(CONVECTOR, max_setpoint=Decimal('23.5')), Decimal('25.0'), Decimal(23)),
        (CONVECTOR, Decimal('3.0'), Decimal(5)),
        (AIR_CONDITIONER, Decimal('20.3'), Decimal('20.5')),
        (AIR_CONDITIONER, Decimal('20.25'), Decimal('20.5')),
        (AIR_CONDITIONER, Decimal('20.2'), Decimal('20.0')),
        (replace(AIR_CONDITIONER, max_setpoint=Decimal('23.7')), Decimal('25.0'), Decimal('23.5')),
    ],
    ids=[
        'half_a_degree_up',
        'below_half_a_degree_down',
        'whole',
        'bias_up',
        'bias_down',
        'capped_at_the_highest_setpoint',
        'cap_rounded_down_to_a_whole_degree',
        'at_least_five',
        'air_conditioner_to_half_a_degree',
        'air_conditioner_half_up',
        'air_conditioner_down',
        'air_conditioner_cap_rounded_down',
    ],
)
def test_setpoint_is_the_target_and_bias_rounded_half_up_to_the_step_within_five_and_the_cap(
    source: SourceSettings,
    target: Decimal,
    setpoint: Decimal,
) -> None:
    assert source_setpoint(target, source) == setpoint


@pytest.mark.parametrize(
    ('on', 'air', 'power'),
    [
        (False, None, True),
        (False, Decimal('21.1'), True),
        (False, Decimal('21.2'), False),
        (True, Decimal('22.4'), True),
        (True, Decimal('22.5'), False),
        (False, Decimal('22.0'), False),
        (True, Decimal('21.5'), True),
    ],
    ids=[
        'no_room_sensor_keeps_it_on',
        'below_the_start',
        'off_within_the_band_stays_off',
        'on_within_the_band_stays_on',
        'a_degree_above_switches_it_off',
        'off_at_the_target_stays_off',
        'on_below_the_target_stays_on',
    ],
)
def test_power_starts_below_the_target_and_stops_a_degree_above_it(
    on: bool,
    air: Decimal | None,
    power: bool,
) -> None:
    assert decide_source_power(on, air, Decimal('21.5')) is power


@pytest.mark.parametrize(
    ('sources', 'states', 'shed', 'outdoor', 'targets'),
    [
        (
            [CONVECTOR, AIR_CONDITIONER],
            {'convector-office': HEATING, 'ac-office': OFF},
            frozenset(),
            Decimal('-5.0'),
            {'convector-office': ON_AT_22, 'ac-office': None},
        ),
        (
            [CONVECTOR, AIR_CONDITIONER],
            {'convector-office': None, 'ac-office': OFF},
            frozenset(),
            Decimal('-5.0'),
            {'convector-office': None, 'ac-office': SourceTarget(power=True, setpoint=Decimal('21.5'))},
        ),
        (
            [CONVECTOR, AIR_CONDITIONER],
            {'convector-office': HEATING, 'ac-office': OFF},
            frozenset({'convector-office'}),
            Decimal('-5.0'),
            {'convector-office': None, 'ac-office': SourceTarget(power=True, setpoint=Decimal('21.5'))},
        ),
        (
            [CONVECTOR, AIR_CONDITIONER],
            {'convector-office': COOLING, 'ac-office': OFF},
            frozenset(),
            Decimal('-5.0'),
            {'convector-office': None, 'ac-office': SourceTarget(power=True, setpoint=Decimal('21.5'))},
        ),
        (
            [replace(CONVECTOR, priority=3), AIR_CONDITIONER],
            {'convector-office': HEATING, 'ac-office': OFF},
            frozenset(),
            Decimal('-17.0'),
            {'convector-office': ON_AT_22, 'ac-office': None},
        ),
        (
            [replace(CONVECTOR, priority=3), AIR_CONDITIONER],
            {'convector-office': HEATING, 'ac-office': OFF},
            frozenset(),
            None,
            {'convector-office': ON_AT_22, 'ac-office': None},
        ),
        (
            [CONVECTOR, replace(AIR_CONDITIONER, commanded_power=True)],
            {'convector-office': OFF, 'ac-office': HEATING},
            frozenset(),
            Decimal('-5.0'),
            {'convector-office': ON_AT_22, 'ac-office': SWITCHED_OFF},
        ),
        (
            [CONVECTOR, AIR_CONDITIONER],
            {'convector-office': OFF, 'ac-office': HEATING},
            frozenset(),
            Decimal('-5.0'),
            {'convector-office': ON_AT_22, 'ac-office': None},
        ),
    ],
    ids=[
        'first_by_priority',
        'unavailable_passed_over',
        'shed_passed_over',
        'owner_mode_passed_over',
        'too_cold_outdoors_for_the_air_conditioner',
        'no_outdoor_reading_for_the_air_conditioner',
        'source_teplo_set_on_but_not_picked_is_switched_off',
        'source_the_owner_switched_on_is_left_alone',
    ],
)
def test_room_picks_its_first_source_that_can_heat_and_switches_off_one_teplo_set_on(
    sources: list[SourceSettings],
    states: dict[str, ClimateUnitState | None],
    shed: frozenset[str],
    outdoor: Decimal | None,
    targets: dict[str, SourceTarget | None],
) -> None:
    assert (
        room_targets(sources, states, shed=shed, air=None, target=Decimal('21.5'), outdoor=outdoor, now=NOW) == targets
    )


def test_room_with_a_sensor_switches_its_source_off_a_degree_above_the_target() -> None:
    targets = room_targets(
        [CONVECTOR],
        {'convector-office': HEATING},
        shed=frozenset(),
        air=Decimal('22.6'),
        target=Decimal('21.5'),
        outdoor=None,
        now=NOW,
    )

    assert targets == {'convector-office': SWITCHED_OFF}


def test_hold_on_any_source_of_a_room_holds_the_room() -> None:
    held = replace(AIR_CONDITIONER, manual_until=NOW + timedelta(hours=1))

    targets = room_targets(
        [CONVECTOR, held],
        {'convector-office': OFF, 'ac-office': OFF},
        shed=frozenset(),
        air=None,
        target=Decimal('21.5'),
        outdoor=Decimal('-5.0'),
        now=NOW,
    )

    assert targets == {'convector-office': None, 'ac-office': None}


@pytest.mark.parametrize(
    ('target', 'state', 'change'),
    [
        (ON_AT_22, OFF, SourceChange(power=True)),
        (SourceTarget(power=True, setpoint=Decimal(19)), OFF, SourceChange(power=True, setpoint=Decimal(19))),
        (SourceTarget(power=True, setpoint=Decimal(19)), HEATING, SourceChange(setpoint=Decimal(19))),
        (ON_AT_22, HEATING, SourceChange()),
        (SWITCHED_OFF, HEATING, SourceChange(power=False)),
        (SWITCHED_OFF, OFF, SourceChange()),
        (ON_AT_22, COOLING, SourceChange()),
    ],
    ids=[
        'switched_on_at_its_setpoint',
        'switched_on_and_set',
        'set',
        'holds_the_target',
        'switched_off',
        'already_off',
        'owner_mode_left_alone',
    ],
)
def test_change_writes_only_what_differs_and_leaves_the_owners_modes_alone(
    target: SourceTarget,
    state: ClimateUnitState,
    change: SourceChange,
) -> None:
    assert plan_source_change(target, state) == change


@pytest.mark.parametrize(
    ('change', 'state', 'starts'),
    [
        (SourceChange(power=True), OFF, True),
        (SourceChange(setpoint=Decimal(24)), AT_ITS_SETPOINT, True),
        (SourceChange(setpoint=Decimal(22)), replace(AT_ITS_SETPOINT, target=Decimal(19)), False),
        (SourceChange(setpoint=Decimal(24)), HEATING, False),
        (SourceChange(setpoint=Decimal(19)), AT_ITS_SETPOINT, False),
        (SourceChange(power=False), HEATING, False),
    ],
    ids=[
        'switched_on',
        'raised_above_its_own_temperature',
        'raised_to_its_own_temperature',
        'raised_while_it_heats',
        'lowered',
        'switched_off',
    ],
)
def test_change_starts_a_source_when_it_switches_it_on_or_raises_it_above_its_own_temperature(
    change: SourceChange,
    state: ClimateUnitState,
    starts: bool,
) -> None:
    assert source_starts(change, state) is starts


@pytest.mark.parametrize(
    ('target', 'state', 'shed', 'hands_off', 'slot_free', 'last_write_at', 'drive'),
    [
        (
            ON_AT_22,
            OFF,
            True,
            True,
            True,
            None,
            SourceDrive(change=SourceChange(), outcome=ZoneCycleOutcome.SHED, starts=False),
        ),
        (
            ON_AT_22,
            OFF,
            False,
            True,
            True,
            None,
            SourceDrive(change=SourceChange(), outcome=ZoneCycleOutcome.HANDS_OFF, starts=False),
        ),
        (
            ON_AT_22,
            None,
            False,
            False,
            True,
            None,
            SourceDrive(change=SourceChange(), outcome=ZoneCycleOutcome.DEVICE_UNAVAILABLE, starts=False),
        ),
        (
            None,
            HEATING,
            False,
            False,
            True,
            None,
            SourceDrive(change=SourceChange(), outcome=ZoneCycleOutcome.UNCHANGED, starts=False),
        ),
        (
            ON_AT_22,
            HEATING,
            False,
            False,
            True,
            None,
            SourceDrive(change=SourceChange(), outcome=ZoneCycleOutcome.UNCHANGED, starts=False),
        ),
        (
            ON_AT_22,
            OFF,
            False,
            False,
            True,
            NOW - timedelta(minutes=4),
            SourceDrive(change=SourceChange(), outcome=ZoneCycleOutcome.THROTTLED, starts=False),
        ),
        (
            ON_AT_22,
            OFF,
            False,
            False,
            False,
            None,
            SourceDrive(change=SourceChange(), outcome=ZoneCycleOutcome.THROTTLED, starts=False),
        ),
        (
            ON_AT_22,
            OFF,
            False,
            False,
            True,
            NOW - INTERVAL,
            SourceDrive(change=SourceChange(power=True), outcome=None, starts=True),
        ),
    ],
    ids=[
        'shed_first',
        'held_by_hand',
        'unavailable',
        'not_driven',
        'already_at_the_target',
        'within_its_interval',
        'slot_taken',
        'its_interval_after_the_last_write',
    ],
)
def test_drive_of_a_source_settles_in_order_shed_hold_reading_change_and_interval(
    target: SourceTarget | None,
    state: ClimateUnitState | None,
    shed: bool,
    hands_off: bool,
    slot_free: bool,
    last_write_at: datetime | None,
    drive: SourceDrive,
) -> None:
    assert (
        plan_source_drive(
            target,
            state,
            last_write_at=last_write_at,
            interval=INTERVAL,
            now=NOW,
            shed=shed,
            hands_off=hands_off,
            slot_free=slot_free,
        )
        == drive
    )


@pytest.mark.parametrize(
    ('target', 'state', 'shed', 'hands_off_until', 'writes_live', 'pending'),
    [
        (ON_AT_22, OFF, True, None, True, PendingWrite(kind=PendingKind.HELD, at=None)),
        (
            ON_AT_22,
            OFF,
            False,
            NOW + timedelta(hours=1),
            True,
            PendingWrite(kind=PendingKind.HANDS_OFF, at=NOW + timedelta(hours=1)),
        ),
        (ON_AT_22, None, False, None, True, PendingWrite(kind=PendingKind.UNREACHED, at=None)),
        (None, HEATING, False, None, True, None),
        (ON_AT_22, HEATING, False, None, True, PendingWrite(kind=PendingKind.HOLDS, at=None)),
        (ON_AT_22, OFF, False, None, False, PendingWrite(kind=PendingKind.WATCHING, at=None)),
        (ON_AT_22, OFF, False, None, True, PendingWrite(kind=PendingKind.NEXT_WRITE, at=NOW + timedelta(minutes=2))),
    ],
    ids=['shed', 'held_by_hand', 'unavailable', 'not_driven', 'holds', 'watching', 'next_write'],
)
def test_pending_write_of_a_source_names_what_keeps_its_target_from_the_unit(
    target: SourceTarget | None,
    state: ClimateUnitState | None,
    shed: bool,
    hands_off_until: datetime | None,
    writes_live: bool,
    pending: PendingWrite | None,
) -> None:
    assert (
        pending_source_write(
            target,
            state,
            last_write_at=NOW - timedelta(minutes=3),
            interval=INTERVAL,
            now=NOW,
            shed=shed,
            hands_off_until=hands_off_until,
            writes_live=writes_live,
        )
        == pending
    )
