from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.air_policy import AirRegulationSettings
from apps.climate.domain.control import ClimateControl
from apps.climate.domain.events import AirControlDryRun, OptimalStartDryRun
from apps.climate.domain.floor_policy import ThermostatTarget
from apps.climate.domain.learning import AirSample, HeatUpModel, Morning, ZoneLearningRecord
from apps.climate.domain.memory import AirDecision, ZoneMemory
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode, ZoneCycleOutcome, ZoneKind
from apps.climate.domain.observation import StopWatch
from apps.climate.domain.optimal_start import OptimalStartSettings, PreheatStart
from apps.climate.domain.second_floor_guard import SecondFloorGuardSettings
from apps.climate.domain.sensors import ClimateSensors
from apps.climate.domain.zone import AirTargets, ComfortWindow, FloorSetpoints, ZoneSettings
from apps.climate.domain.zone_cycle import (
    CycleConditions,
    ZoneConditions,
    ZoneDecision,
    ZoneFollowUp,
    decide_zone,
    follow_zone,
)

MOSCOW = ZoneInfo('Europe/Moscow')
COMFORT_AT = datetime(2026, 11, 2, 6, 30, tzinfo=MOSCOW)
FIXED_START = datetime(2026, 11, 2, 3, 30, tzinfo=MOSCOW)
OPTIMAL_START = datetime(2026, 11, 2, 3, 50, tzinfo=MOSCOW)
MORNING = datetime(2026, 11, 2, 7, 0, tzinfo=MOSCOW)
AIR_MODE_STOP = datetime(2026, 11, 2, 5, 40, tzinfo=MOSCOW)
NIGHT = datetime(2026, 11, 2, 1, 0, tzinfo=MOSCOW)
OUTDOOR = Decimal('-3.4')
TARGET = Decimal('22.0')
CONTROL = ClimateControl(
    sensors=ClimateSensors(outdoor_slug='outdoor', second_floor_slug='air-second-floor'),
    regulation=AirRegulationSettings(start_offset=Decimal('0.3'), initial_early_stop_offset=Decimal('0.3')),
    optimal_start=OptimalStartSettings(lead_cap=timedelta(hours=4), mornings_needed=3),
    second_floor_guard=SecondFloorGuardSettings(
        zone_slug='bathroom-2f',
        threshold=Decimal('19.0'),
        hysteresis=Decimal('0.3'),
        live=False,
    ),
)
UNLEARNED = ZoneLearningRecord.unlearned()
LEARNED = ZoneLearningRecord(
    early_stop_offset=None,
    heat_up=HeatUpModel(dead_time=timedelta(minutes=40), rates={-5: Decimal('0.800')}, mornings=3),
)
COMFORT = ThermostatTarget(power=True, setpoint=Decimal('28.0'))
HEAT = ThermostatTarget(power=True, setpoint=Decimal('29.0'))
IDLE = ThermostatTarget(power=True, setpoint=Decimal('18.0'))
HEATING = AirDecision(state=AirState.HEAT, target=HEAT)


def _cycle(now: datetime, *, clock_trusted: bool = True) -> CycleConditions:
    return CycleConditions(now=now, clock_trusted=clock_trusted, control=CONTROL, outdoor=OUTDOOR)


def _conditions(
    air: str | None,
    *,
    learning: ZoneLearningRecord = UNLEARNED,
    guarded: bool = False,
    writes_live: bool = True,
) -> ZoneConditions:
    return ZoneConditions(
        air=None if air is None else Decimal(air),
        learning=learning,
        guarded=guarded,
        writes_live=writes_live,
    )


def _follow(
    zone: ZoneSettings,
    memory: ZoneMemory,
    conditions: ZoneConditions,
    cycle: CycleConditions,
    outcome: ZoneCycleOutcome,
) -> ZoneFollowUp:
    return follow_zone(zone, memory, decide_zone(zone, memory, conditions, cycle), outcome, conditions, cycle)


@pytest.fixture
def f_zone() -> ZoneSettings:
    return ZoneSettings(
        slug='kitchen',
        name='Kitchen',
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
            limit=Decimal('29.0'),
        ),
        air_targets=AirTargets(comfort=TARGET, night=Decimal('19.5')),
        preheat_lead=timedelta(hours=3),
        windows=tuple(ComfortWindow(weekday=weekday, start=time(6, 30), end=time(23, 30)) for weekday in range(7)),
        boost_until=None,
        last_command_at=None,
        last_dry_run_at=None,
        last_air_dry_run_at=None,
    )


@pytest.fixture
def f_live_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, air_control_live=True)


@pytest.fixture
def f_boosted_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, boost_until=MORNING + timedelta(hours=1))


@pytest.fixture
def f_floor_zone(f_zone: ZoneSettings) -> ZoneSettings:
    return replace(f_zone, slug='bathroom-2f', kind=ZoneKind.FLOOR, air_sensor_slug=None)


@pytest.fixture
def f_heating_memory() -> ZoneMemory:
    return ZoneMemory(path=ControlPath.AIR, mode=ScheduleMode.COMFORT, air_state=AirState.HEAT, heat_target=TARGET)


@pytest.fixture
def f_journaled_memory() -> ZoneMemory:
    return ZoneMemory(
        path=ControlPath.AIR_DRY_RUN,
        mode=ScheduleMode.COMFORT,
        air_state=AirState.HEAT,
        would_be=HEATING,
    )


@pytest.fixture
def f_watched_memory() -> ZoneMemory:
    return ZoneMemory(
        path=ControlPath.AIR,
        mode=ScheduleMode.COMFORT,
        air_state=AirState.IDLE,
        heat_target=TARGET,
        stop=StopWatch(stopped_at=MORNING, target=TARGET, peak=Decimal('22.3')),
    )


@pytest.fixture
def f_latched_memory() -> ZoneMemory:
    return ZoneMemory(
        path=ControlPath.AIR_DRY_RUN,
        mode=ScheduleMode.NIGHT,
        air_state=AirState.IDLE,
        would_be=AirDecision(state=AirState.IDLE, target=IDLE),
        preheat=PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=2, minutes=40)),
    )


@pytest.fixture
def f_preheating_memory() -> ZoneMemory:
    return ZoneMemory(
        path=ControlPath.AIR,
        mode=ScheduleMode.PREHEAT,
        air_state=AirState.HEAT,
        heat_target=TARGET,
        preheat=PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=2, minutes=40)),
    )


@pytest.fixture
def f_morning() -> Morning:
    return Morning(
        comfort_at=COMFORT_AT,
        target=TARGET,
        outdoor=OUTDOOR,
        samples=(AirSample(at=FIXED_START, air=Decimal('20.0')),),
    )


@pytest.fixture
def f_preheating_morning_memory(f_preheating_memory: ZoneMemory, f_morning: Morning) -> ZoneMemory:
    return replace(f_preheating_memory, morning=f_morning)


def test_zone_without_air_reading_runs_floor_mode(f_live_zone: ZoneSettings) -> None:
    decision = decide_zone(f_live_zone, ZoneMemory(), _conditions(None), _cycle(MORNING))

    assert decision == ZoneDecision(path=ControlPath.FLOOR, plan=None, mode=ScheduleMode.COMFORT, target=COMFORT)


def test_air_zone_in_dry_run_keeps_floor_mode_on_its_fixed_lead(f_zone: ZoneSettings) -> None:
    decision = decide_zone(f_zone, ZoneMemory(), _conditions('20.4', learning=LEARNED), _cycle(FIXED_START))

    would_be_mode = None if decision.plan is None else decision.plan.mode
    assert (decision.path, decision.mode, decision.target, would_be_mode) == (
        ControlPath.AIR_DRY_RUN,
        ScheduleMode.PREHEAT,
        COMFORT,
        ScheduleMode.NIGHT,
    )


def test_live_air_zone_drives_air_actuation(f_live_zone: ZoneSettings) -> None:
    decision = decide_zone(f_live_zone, ZoneMemory(), _conditions('21.5'), _cycle(MORNING))

    assert (decision.path, decision.mode, decision.target) == (ControlPath.AIR, ScheduleMode.COMFORT, HEAT)


@pytest.mark.parametrize(
    ('guarded', 'target'),
    [(True, COMFORT), (False, ThermostatTarget(power=True, setpoint=Decimal('22.0')))],
    ids=['guard_raises_night_to_comfort', 'unguarded_keeps_night'],
)
def test_guard_raises_guarded_zone_to_its_comfort_floor_setpoint(
    f_floor_zone: ZoneSettings,
    guarded: bool,
    target: ThermostatTarget,
) -> None:
    decision = decide_zone(f_floor_zone, ZoneMemory(), _conditions(None, guarded=guarded), _cycle(NIGHT))

    assert decision.target == target


@pytest.mark.parametrize(
    ('outcome', 'state'),
    [
        (ZoneCycleOutcome.APPLIED, AirState.HEAT),
        (ZoneCycleOutcome.UNCHANGED, AirState.HEAT),
        (ZoneCycleOutcome.DRY_RUN, AirState.HEAT),
        (ZoneCycleOutcome.THROTTLED, None),
        (ZoneCycleOutcome.FAILED, None),
        (ZoneCycleOutcome.DEVICE_UNAVAILABLE, None),
    ],
)
def test_live_air_state_follows_only_a_thermostat_that_follows(
    f_live_zone: ZoneSettings,
    outcome: ZoneCycleOutcome,
    state: AirState | None,
) -> None:
    follow_up = _follow(f_live_zone, ZoneMemory(), _conditions('21.5'), _cycle(MORNING), outcome)

    assert (follow_up.memory.path, follow_up.memory.air_state, follow_up.memory.heat_target) == (
        ControlPath.AIR,
        state,
        TARGET,
    )


def test_applied_stop_of_heat_starts_stop_watch(f_live_zone: ZoneSettings, f_heating_memory: ZoneMemory) -> None:
    follow_up = _follow(
        f_live_zone,
        f_heating_memory,
        _conditions('21.8'),
        _cycle(MORNING),
        ZoneCycleOutcome.APPLIED,
    )

    assert (follow_up.memory.air_state, follow_up.memory.stop) == (
        AirState.IDLE,
        StopWatch(stopped_at=MORNING, target=TARGET, peak=Decimal('21.8')),
    )


def test_stop_written_in_dry_run_starts_no_stop_watch(f_live_zone: ZoneSettings, f_heating_memory: ZoneMemory) -> None:
    follow_up = _follow(
        f_live_zone,
        f_heating_memory,
        _conditions('21.8', writes_live=False),
        _cycle(MORNING),
        ZoneCycleOutcome.DRY_RUN,
    )

    assert (follow_up.memory.air_state, follow_up.memory.stop) == (AirState.IDLE, None)


def test_stop_watch_hands_over_its_peak_two_hours_after_stop(
    f_live_zone: ZoneSettings,
    f_watched_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_live_zone,
        f_watched_memory,
        _conditions('21.9'),
        _cycle(MORNING + timedelta(hours=2, minutes=1)),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.stop, follow_up.memory.stop) == (
        StopWatch(stopped_at=MORNING, target=TARGET, peak=Decimal('22.3')),
        None,
    )


def test_air_dry_run_journals_first_decision(f_zone: ZoneSettings) -> None:
    follow_up = _follow(f_zone, ZoneMemory(), _conditions('21.5'), _cycle(MORNING), ZoneCycleOutcome.UNCHANGED)

    assert (follow_up.air_dry_run, follow_up.memory.would_be, follow_up.memory.air_state) == (
        AirControlDryRun(
            zone='kitchen',
            air=Decimal('21.5'),
            target=TARGET,
            mode=ScheduleMode.COMFORT,
            state=AirState.HEAT,
            previous_state=None,
            power=True,
            setpoint=Decimal('29.0'),
            early_stop_offset=Decimal('0.3'),
            lead_minutes=None,
            occurred_at=MORNING,
        ),
        HEATING,
        AirState.HEAT,
    )


def test_air_dry_run_journals_would_be_thermostat_change_beside_state_it_keeps(
    f_boosted_zone: ZoneSettings,
    f_journaled_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_boosted_zone,
        f_journaled_memory,
        _conditions('21.5'),
        _cycle(MORNING),
        ZoneCycleOutcome.APPLIED,
    )

    assert follow_up.air_dry_run == AirControlDryRun(
        zone='kitchen',
        air=Decimal('21.5'),
        target=TARGET,
        mode=ScheduleMode.COMFORT,
        state=AirState.HEAT,
        previous_state=AirState.HEAT,
        power=True,
        setpoint=Decimal('30.0'),
        early_stop_offset=Decimal('0.3'),
        lead_minutes=None,
        occurred_at=MORNING,
    )


def test_air_dry_run_journals_nothing_while_decision_holds(
    f_zone: ZoneSettings,
    f_journaled_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_zone,
        f_journaled_memory,
        _conditions('21.6'),
        _cycle(MORNING),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.air_dry_run, follow_up.memory.would_be) == (None, HEATING)


@pytest.mark.parametrize(
    ('since_journaled', 'state'),
    [(timedelta(minutes=10), AirState.HEAT), (timedelta(minutes=30), AirState.IDLE)],
    ids=['within_thirty_minutes', 'after_thirty_minutes'],
)
def test_air_dry_run_change_waits_thirty_minutes_after_last_one(
    f_zone: ZoneSettings,
    f_journaled_memory: ZoneMemory,
    since_journaled: timedelta,
    state: AirState,
) -> None:
    zone = replace(f_zone, last_air_dry_run_at=MORNING - since_journaled)

    follow_up = _follow(zone, f_journaled_memory, _conditions('22.0'), _cycle(MORNING), ZoneCycleOutcome.UNCHANGED)

    assert (follow_up.memory.air_state, follow_up.air_dry_run is not None) == (state, state is AirState.IDLE)


def test_air_dry_run_change_waits_for_available_thermostat(
    f_zone: ZoneSettings,
    f_journaled_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_zone,
        f_journaled_memory,
        _conditions('22.0'),
        _cycle(MORNING),
        ZoneCycleOutcome.DEVICE_UNAVAILABLE,
    )

    assert (follow_up.air_dry_run, follow_up.memory.air_state) == (None, AirState.HEAT)


def test_air_dry_run_journals_optimal_start_when_preheat_would_begin(f_zone: ZoneSettings) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.PREHEAT)

    follow_up = _follow(
        f_zone,
        memory,
        _conditions('20.4', learning=LEARNED),
        _cycle(OPTIMAL_START),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.optimal_start, follow_up.memory.preheat) == (
        OptimalStartDryRun(
            zone='kitchen',
            lead_minutes=160,
            start=OPTIMAL_START,
            fixed_start=FIXED_START,
            occurred_at=OPTIMAL_START,
        ),
        PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=2, minutes=40)),
    )


def test_air_dry_run_journals_later_optimal_start_when_floor_mode_preheat_begins(f_zone: ZoneSettings) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.NIGHT)

    follow_up = _follow(
        f_zone,
        memory,
        _conditions('20.4', learning=LEARNED),
        _cycle(FIXED_START),
        ZoneCycleOutcome.APPLIED,
    )

    assert (follow_up.optimal_start, follow_up.memory.preheat) == (
        OptimalStartDryRun(
            zone='kitchen',
            lead_minutes=160,
            start=OPTIMAL_START,
            fixed_start=FIXED_START,
            occurred_at=FIXED_START,
        ),
        PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=2, minutes=40)),
    )


def test_air_dry_run_journals_optimal_start_once_per_morning(
    f_zone: ZoneSettings,
    f_latched_memory: ZoneMemory,
) -> None:
    follow_up = _follow(
        f_zone,
        f_latched_memory,
        _conditions('21.0', learning=LEARNED),
        _cycle(OPTIMAL_START + timedelta(minutes=30)),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.optimal_start, follow_up.memory.preheat) == (None, f_latched_memory.preheat)


def test_floor_mode_preheat_of_dry_run_zone_starts_morning(f_zone: ZoneSettings) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.NIGHT)

    follow_up = _follow(f_zone, memory, _conditions('20.0'), _cycle(FIXED_START), ZoneCycleOutcome.APPLIED)

    assert follow_up.memory.morning == Morning(
        comfort_at=COMFORT_AT,
        target=TARGET,
        outdoor=OUTDOOR,
        samples=(AirSample(at=FIXED_START, air=Decimal('20.0')),),
    )


@pytest.mark.parametrize(
    'outcome',
    [ZoneCycleOutcome.THROTTLED, ZoneCycleOutcome.DEVICE_UNAVAILABLE, ZoneCycleOutcome.FAILED],
)
def test_preheat_the_thermostat_does_not_take_starts_no_morning(
    f_zone: ZoneSettings,
    outcome: ZoneCycleOutcome,
) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.NIGHT)

    follow_up = _follow(f_zone, memory, _conditions('20.0'), _cycle(FIXED_START), outcome)

    assert follow_up.memory.morning is None


def test_morning_is_dropped_when_its_thermostat_is_lost(f_zone: ZoneSettings, f_morning: Morning) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.PREHEAT, morning=f_morning)

    follow_up = _follow(
        f_zone,
        memory,
        _conditions('20.4'),
        _cycle(FIXED_START + timedelta(hours=1)),
        ZoneCycleOutcome.DEVICE_UNAVAILABLE,
    )

    assert (follow_up.morning, follow_up.memory.morning) == (None, None)


def test_morning_without_live_writes_is_not_observed(f_zone: ZoneSettings) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.NIGHT)

    follow_up = _follow(
        f_zone,
        memory,
        _conditions('20.0', writes_live=False),
        _cycle(FIXED_START),
        ZoneCycleOutcome.DRY_RUN,
    )

    assert follow_up.memory.morning is None


def test_mode_seen_on_untrusted_clock_starts_no_morning(f_zone: ZoneSettings) -> None:
    follow_up = _follow(
        f_zone,
        ZoneMemory(),
        _conditions('20.0'),
        _cycle(NIGHT, clock_trusted=False),
        ZoneCycleOutcome.APPLIED,
    )

    assert follow_up.memory.mode is None


def test_finished_morning_is_handed_over_once(f_zone: ZoneSettings, f_morning: Morning) -> None:
    memory = ZoneMemory(path=ControlPath.AIR_DRY_RUN, mode=ScheduleMode.PREHEAT, morning=f_morning)

    follow_up = _follow(f_zone, memory, _conditions('21.9'), _cycle(COMFORT_AT), ZoneCycleOutcome.UNCHANGED)

    assert (follow_up.morning, follow_up.memory.morning) == (f_morning, None)


def test_untrusted_clock_keeps_held_preheat(f_live_zone: ZoneSettings, f_preheating_memory: ZoneMemory) -> None:
    follow_up = _follow(
        f_live_zone,
        f_preheating_memory,
        _conditions('21.5', learning=LEARNED),
        _cycle(COMFORT_AT - timedelta(hours=1, minutes=30), clock_trusted=False),
        ZoneCycleOutcome.UNCHANGED,
    )

    assert (follow_up.memory.preheat, follow_up.memory.air_state) == (f_preheating_memory.preheat, AirState.HEAT)


def test_air_mode_stop_in_preheat_finishes_morning(
    f_live_zone: ZoneSettings,
    f_preheating_morning_memory: ZoneMemory,
    f_morning: Morning,
) -> None:
    follow_up = _follow(
        f_live_zone,
        f_preheating_morning_memory,
        _conditions('21.7', learning=LEARNED),
        _cycle(AIR_MODE_STOP),
        ZoneCycleOutcome.APPLIED,
    )

    assert (follow_up.morning, follow_up.memory.morning, follow_up.memory.air_state) == (
        replace(f_morning, samples=(*f_morning.samples, AirSample(at=AIR_MODE_STOP, air=Decimal('21.7')))),
        None,
        AirState.IDLE,
    )


def test_air_mode_stop_waiting_its_turn_keeps_morning_going(
    f_live_zone: ZoneSettings,
    f_preheating_morning_memory: ZoneMemory,
    f_morning: Morning,
) -> None:
    follow_up = _follow(
        f_live_zone,
        f_preheating_morning_memory,
        _conditions('21.7', learning=LEARNED),
        _cycle(AIR_MODE_STOP),
        ZoneCycleOutcome.THROTTLED,
    )

    assert (follow_up.morning, follow_up.memory.morning, follow_up.memory.air_state) == (
        None,
        replace(f_morning, samples=(*f_morning.samples, AirSample(at=AIR_MODE_STOP, air=Decimal('21.7')))),
        AirState.HEAT,
    )


def test_lost_air_sensor_keeps_latched_preheat_and_forgets_air_state(
    f_live_zone: ZoneSettings,
    f_watched_memory: ZoneMemory,
) -> None:
    memory = replace(f_watched_memory, preheat=PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=3)))

    follow_up = _follow(f_live_zone, memory, _conditions(None), _cycle(MORNING), ZoneCycleOutcome.APPLIED)

    assert follow_up.memory == ZoneMemory(
        path=ControlPath.FLOOR,
        mode=ScheduleMode.COMFORT,
        preheat=PreheatStart(comfort_at=COMFORT_AT, lead=timedelta(hours=3)),
    )
